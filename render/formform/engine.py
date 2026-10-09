"""Kare üretici ve video yazıcı.

İki geçiş:
1. Analiz: her karenin ölçeği (sabit görsel kütle) ve pozlaması 1/4 çözünürlükte hesaplanır,
   sonra zamanda yumuşatılır. Böylece kareler birbirinden bağımsız çizilebilir ve titreme olmaz.
2. Çizim: kareler çekirdeklere dağıtılır, tam çözünürlükte çizilir, sırayla ffmpeg'e yazılır.
"""
import json
import multiprocessing as mp
import os
import shutil
import subprocess
import sys
import time

import numpy as np
from scipy.ndimage import gaussian_filter1d

from . import audio, library as L, raster
from .geometry import TAU, build_cloud
from .timeline import Plan

DEFAULTS = dict(duration=6.0, fps=30, width=720, height=1280, loop='seamless', start='peak', sound=None)
MAX_DURATION = 32.0
TARGET_EXPOSURE = 0.90
ANALYSIS_DIV = 2        # analiz geçişi çözünürlük böleni (ince teller 1/4'te yanlış ölçülür)
ANALYSIS_STEP = 3       # analiz her 3 karede bir, arası aradeğerlenir


def normalize_spec(spec):
    s = dict(DEFAULTS)
    s.update({k: v for k, v in spec.items() if v is not None})
    for key in ('video_id', 'structure', 'palette', 'motion_quality', 'lighting'):
        if not s.get(key):
            raise ValueError(f"spec.{key} gerekli")
    s['materials'] = list(s.get('materials') or [])
    s['transformations'] = list(s.get('transformations') or [])
    s['duration'] = float(np.clip(float(s['duration']), 2.0, MAX_DURATION))
    if s['loop'] not in ('seamless', 'forward'):
        raise ValueError("spec.loop 'seamless' ya da 'forward' olmalı")
    if s['start'] not in ('peak', 'base'):
        raise ValueError("spec.start 'peak' ya da 'base' olmalı")
    if s['sound'] in ('', 'none'):
        s['sound'] = None
    if s['sound'] is not None and s['sound'] not in L.SOUNDS:
        raise ValueError(f"spec.sound bilinmiyor: {s['sound']!r} (seçenekler: {sorted(L.SOUNDS)})")
    s['fps'] = int(np.clip(int(s['fps']), 12, 60))
    s['width'] = int(s['width']) // 4 * 4
    s['height'] = int(s['height']) // 4 * 4
    if not (160 <= s["width"] <= 1440 and 160 <= s["height"] <= 2560):
        raise ValueError('çözünürlük sınır dışı')
    return s


class Renderer:
    def __init__(self, spec):
        self.spec = normalize_spec(spec)
        self.plan = Plan(self.spec)
        self.W, self.H = self.spec['width'], self.spec['height']
        self.res = self.W / 720.0     # parlama/yumuşatma yarıçapları çözünürlükle orantılı: her boyutta aynı görünüm

    def _cloud(self, tau, W, H, sc_hint):
        knobs, style, _ = self.plan.state(tau)
        cloud = build_cloud(knobs, style, tau, self.plan.rig, sc_hint)
        return cloud, style, tau

    def _accumulate(self, cloud, style, tau, sc, W, H, center):
        p = self.plan
        sx, sy, color, weight, z, a = raster.shade(
            cloud, style, p.light, p.palette, sc, W, H, p.rig['noise_phase'], TAU * tau,
            p.rig['smoke_cycles'], center)
        acc = raster.splat(sx, sy, color, weight, W, H)
        if style['opaque'] > 0.01:
            opq, cov = raster.splat_opaque(sx, sy, color, z, a, W, H)
        else:
            opq, cov = np.zeros((H, W, 3), np.float32), np.zeros((H, W), np.float32)
        return acc, opq, cov

    def analyze(self, tau):
        """Karenin ölçeği (px/birim, tam çözünürlük), merkezi, pozlama kazancı ve parlaklık histogramı.
        Yarı çözünürlükte çalışır."""
        d = ANALYSIS_DIV
        Wa, Ha = self.W // d, self.H // d
        cloud, style, tau = self._cloud(tau, Wa, Ha, 0.30 * Wa)
        sca, center = raster.fit_scale(cloud.pos, cloud.alpha, Wa, Ha)
        acc, opq, cov = self._accumulate(cloud, style, tau, sca, Wa, Ha, center)
        lin = raster.radiance(acc, opq, cov, style, scale=self.res / d)
        stat = raster.exposure_stat(lin)
        gain = float(np.clip(TARGET_EXPOSURE / max(stat, 1e-4), 0.06, 3.2))
        return sca * d, gain, center, raster.luma_hist(lin)

    def render(self, tau, sc, gain, center, index):
        """Tam çözünürlüklü kare: (H, W, 3) float32, 0..1."""
        W, H = self.W, self.H
        cloud, style, tau = self._cloud(tau, W, H, sc)
        acc, opq, cov = self._accumulate(cloud, style, tau, sc, W, H, center)
        lin = raster.radiance(acc, opq, cov, style, scale=self.res)
        rng = np.random.default_rng([self.plan.seed, index])
        return raster.finish(lin, gain, self.plan.light, rng)

    def frame(self, tau, index=0):
        """Tek kare (önizleme): analiz + çizim, zaman yumuşatması yok."""
        sc, gain, center, _ = self.analyze(tau)
        return self.render(tau, sc, gain, center, index)

    def taus(self):
        n = int(round(self.spec['duration'] * self.spec['fps']))
        if self.spec['loop'] == 'seamless':      # tau = 1 kare 0 ile aynı: tekrar etmesin
            return [i / n for i in range(n)]
        return [i / (n - 1) for i in range(n)]


# ---- paralel işçiler (fork ile kalıtım) -------------------------------------
_R = None
_SC = None
_GAIN = None
_CEN = None
_TAUS = None
_ORDER = None      # çıktı karesi i, kaynak (zaman) karesi _ORDER[i]'yi çizer: döngü en etkileyici andan başlar


def _analyze_worker(i):
    return _R.analyze(_TAUS[i])


def _render_worker(i):
    src = _ORDER[i]
    img = _R.render(_TAUS[src], _SC[src], _GAIN[src], (_CEN[src, 0], _CEN[src, 1]), i)
    bad = not np.isfinite(img).all()
    if bad:     # bozuk video yayınlamaktansa temizle ve raporla
        img = np.nan_to_num(img, nan=0.0, posinf=1.0, neginf=0.0)
    lum = img.max(axis=-1)
    stats = (float(lum.mean()), float((lum > 0.15).mean()), lum[::16, ::16].astype(np.float32),
             audio.frame_features(img) if _R.spec['sound'] else None)     # ses bitmiş kareyi izler
    return (img * 255.0 + 0.5).astype(np.uint8).tobytes(), bad, stats


def _ffmpeg_cmd(out_path, W, H, fps):
    exe = shutil.which('ffmpeg')
    if not exe:
        raise RuntimeError('ffmpeg bulunamadı')
    return [exe, '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{W}x{H}',
            '-r', str(fps), '-i', '-', '-an',
            '-vf', 'scale=out_color_matrix=bt709:out_range=tv,format=yuv420p',
            '-c:v', 'libx264', '-preset', 'medium', '-crf', '17', '-profile:v', 'high',
            '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709', '-color_range', 'tv',
            '-movflags', '+faststart', out_path]


def _mux_audio(video_path, wav_path, out_path):
    exe = shutil.which('ffmpeg')
    cmd = [exe, '-y', '-loglevel', 'error', '-i', video_path, '-i', wav_path, '-map', '0:v:0', '-map', '1:a:0',
           '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-ar', str(audio.SR), '-ac', '2',
           '-movflags', '+faststart', '-shortest', out_path]
    if subprocess.run(cmd).returncode != 0:
        raise RuntimeError('ffmpeg ses birleştirme hatası')


# Kalite kapısı: bariz bozuk çıktıyı (kapkara, boş, donuk) yayına bırakmaz. Eşikler gerçek videoların çok altındadır.
QUALITY_MIN_LUMA = 0.020
QUALITY_MIN_LIT = 0.030
QUALITY_MIN_MOTION = 0.004


def quality_report(stats):
    """stats: kare başına (ortalama parlaklık, aydınlık alan oranı, küçük parlaklık görüntüsü, ...)."""
    luma = np.array([x[0] for x in stats])
    lit = np.array([x[1] for x in stats])
    thumbs = np.stack([x[2] for x in stats])
    motion = np.abs(np.diff(thumbs, axis=0)).mean(axis=(1, 2)) if len(stats) > 1 else np.zeros(1)
    rep = dict(luma_mean=round(float(luma.mean()), 4), luma_min=round(float(luma.min()), 4),
               lit_mean=round(float(lit.mean()), 4), motion_mean=round(float(motion.mean()), 5))
    bad = []
    if rep['luma_mean'] < QUALITY_MIN_LUMA:
        bad.append(f"video çok karanlık (ortalama parlaklık {rep['luma_mean']})")
    if rep['lit_mean'] < QUALITY_MIN_LIT:
        bad.append(f"kareler neredeyse boş (aydınlık alan {rep['lit_mean']})")
    if rep['motion_mean'] < QUALITY_MIN_MOTION:
        bad.append(f"video hareketsiz (hareket {rep['motion_mean']})")
    return rep, bad


def render_video(spec, out_path, workers=None, log=sys.stderr):
    global _R, _SC, _GAIN, _CEN, _TAUS, _ORDER
    _R = Renderer(spec)
    s = _R.spec
    _TAUS = _R.taus()
    n = len(_TAUS)
    workers = workers or int(os.environ.get('FORMFORM_WORKERS') or min(os.cpu_count() or 1, 4))
    for w in _R.plan.warnings:
        print('UYARI:', w, file=log)

    t0 = time.time()
    ctx = mp.get_context('fork')
    seamless = s['loop'] == 'seamless'
    probe = sorted(set(range(0, n, ANALYSIS_STEP)) | (set() if seamless else {n - 1}))
    with ctx.Pool(workers) as pool:
        res = pool.map(_analyze_worker, probe, chunksize=1)
    frames = np.arange(n)
    per = n if seamless else None          # seamless: aradeğer ve yumuşatma sarmal, dikiş görünmez
    sc = np.exp(np.interp(frames, probe, np.log([r[0] for r in res]), period=per))
    gain = np.exp(np.interp(frames, probe, np.log([r[1] for r in res]), period=per))
    cen = np.stack([np.interp(frames, probe, [r[2][k] for r in res], period=per) for k in (0, 1)], axis=1)
    mode = 'wrap' if seamless else 'nearest'
    # zamanda yumuşat: ölçek, merkez ve pozlama titremesin
    _SC = np.exp(gaussian_filter1d(np.log(sc), 5.0, mode=mode))
    _GAIN = np.exp(gaussian_filter1d(np.log(gain), 7.0, mode=mode))
    _CEN = gaussian_filter1d(cen, 6.0, axis=0, mode=mode)
    # Kusursuz döngüde başlangıç noktası serbesttir: video en etkileyici (parlak, dolu) anla açılır.
    # İlk saniye izleyiciyi tutar, ilk kare de kapak olur.
    # Doluluk, gerçek renderda kullanılan (zamanda yumuşatılmış) kazançla hesaplanır: analizdeki ham kazançla değil.
    start = 0
    if seamless and s['start'] == 'peak':
        light = _R.plan.light
        at_probe = np.array([raster.hist_interest(r[3], _GAIN[i], light) for i, r in zip(probe, res)])
        interest = np.interp(frames, probe, at_probe, period=per)
        start = int(np.argmax(gaussian_filter1d(interest, max(1.0, s['fps'] * 0.12), mode='wrap')))
    _ORDER = (np.arange(n) + start) % n
    print(f'analiz {time.time() - t0:.0f}s  ölçek {_SC.min():.0f}-{_SC.max():.0f}  '
          f'kazanç {_GAIN.min():.2f}-{_GAIN.max():.2f}', file=log, flush=True)

    # işçiler düzeltilmiş _SC/_GAIN değerlerini fork ile devralsın diye havuz yeniden açılır
    nan_frames = 0
    stats = []
    video_path = out_path if not s['sound'] else out_path + '.sessiz.mp4'
    p = subprocess.Popen(_ffmpeg_cmd(video_path, _R.W, _R.H, s['fps']), stdin=subprocess.PIPE)
    with ctx.Pool(workers) as pool:
        try:
            for i, (data, bad, st) in enumerate(pool.imap(_render_worker, range(n), chunksize=1)):
                nan_frames += int(bad)
                stats.append(st)
                p.stdin.write(data)
                if i % 30 == 0:
                    print(f'kare {i + 1}/{n}  {time.time() - t0:.0f}s', file=log, flush=True)
            p.stdin.close()
        except BrokenPipeError:
            pass
    if p.wait() != 0:
        raise RuntimeError('ffmpeg hata verdi')
    quality, problems = quality_report(stats)
    if problems:
        raise RuntimeError('kalite kapısı: ' + '; '.join(problems))

    loudness = None
    if s['sound']:
        # Ses, görüntünün zaman çizelgesinden ve bitmiş karelerin ölçülerinden (hareket, parlama, doluluk, ışık
        # dengesi) üretilir, video döngüde nereden başladıysa oradan başlar
        samples = audio.n_samples(s['duration'], s['fps'])
        visual = audio.visual_curves([st[3] for st in stats], start, seamless)
        track = audio.synthesize(_R.plan, s['sound'], samples, start_fraction=start / n, visual=visual)
        loudness = round(audio.loudness(track), 2)
        wav = out_path + '.wav'
        audio.write_wav(wav, track, seed=_R.plan.seed)
        try:
            _mux_audio(video_path, wav, out_path)
        finally:
            for tmp in (wav, video_path):
                if os.path.exists(tmp):
                    os.remove(tmp)
    info = dict(video_id=s['video_id'], seed=_R.plan.seed, frames=n, start_frame=start,
                seconds=round(time.time() - t0, 1), sound=s['sound'], loudness_lufs=loudness, quality=quality,
                workers=workers, nan_frames=nan_frames, warnings=_R.plan.warnings)
    print(json.dumps(info, ensure_ascii=False), file=log)
    return info
