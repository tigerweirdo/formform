"""Kompozisyon, açılış anı, çözünürlük tutarlılığı, ses birleştirme, kalite kapısı ve check.py."""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, 'render'))
import check                                       # noqa: E402
from formform import engine, library as L          # noqa: E402
from formform.engine import Renderer, normalize_spec, quality_report, render_video   # noqa: E402

SPEC = dict(video_id='FF_COMP_1', structure='floating core', palette='scarlet-burgundy',
            motion_quality='controlled but progressively more energetic',
            lighting='subtle rim lighting emphasizing translucent edges',
            materials=['fine glowing threads', 'dense glossy sculptural surfaces', 'smooth liquid glass',
                       'translucent flowing silk-like surfaces', 'soft reflective curved sheets'],
            transformations=['collapse into narrow luminous strands',
                             'transform from smooth surfaces into angular crystalline structures',
                             'liquefy into smooth flowing curves', 'split into several thin connected flowing ribbons'],
            duration=3, fps=12, width=180, height=320, loop='seamless')


def small_render(path, **kw):
    spec = dict(SPEC, **kw)
    return render_video(spec, path, workers=2, log=io.StringIO())


def gray_frames(path, w, h):
    raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', path, '-f', 'rawvideo', '-pix_fmt', 'gray', '-'])
    return np.frombuffer(raw, dtype=np.uint8).reshape(-1, h, w).astype(np.float64) / 255.0


class VerticalFill(unittest.TestCase):
    def test_form_fills_the_vertical_frame_and_sits_a_little_high(self):
        """9:16 kareyi iyi doldurur ve Instagram arayüzünün kapattığı alt kenardan uzak durur."""
        from datetime import datetime, timezone
        import random
        sys.path.insert(0, ROOT)
        from ops import evolve
        rng = random.Random(5)
        spans, centers = [], []
        for i in range(8):
            r = dict(evolve.generate([], [], rng.random, datetime(2026, 10, 3, 3, 30, i, tzinfo=timezone.utc))
                     ['release_payload'], width=180, height=320, sound=None)
            img = Renderer(r).frame(0.4)
            lit = img.max(axis=-1) > 0.08
            rows = np.where(lit.any(axis=1))[0]
            spans.append((rows[-1] - rows[0]) / lit.shape[0])
            centers.append(np.where(lit)[0].mean() / lit.shape[0])
        self.assertGreater(float(np.mean(spans)), 0.60, f'dikey kapsam düşük: {np.mean(spans):.2f}')
        self.assertGreater(float(min(spans)), 0.28)
        self.assertTrue(0.44 < float(np.mean(centers)) < 0.50, f'dikey merkez {np.mean(centers):.3f}')


class ResolutionConsistency(unittest.TestCase):
    def test_same_look_at_different_resolutions(self):
        """Parlama yarıçapları çözünürlükle orantılı: 1080p'de görünüm 720p'ye benzer (nokta dokusu çıkmaz)."""
        a = Renderer(dict(SPEC, width=360, height=640)).frame(0.3)
        b = Renderer(dict(SPEC, width=540, height=960)).frame(0.3)
        self.assertEqual(Renderer(dict(SPEC, width=1080, height=1920)).res, 1.5)
        self.assertAlmostEqual(float(b.mean() / a.mean()), 1.0, delta=0.12)
        lit_a, lit_b = float((a.max(axis=-1) > 0.15).mean()), float((b.max(axis=-1) > 0.15).mean())
        self.assertAlmostEqual(lit_b / lit_a, 1.0, delta=0.15)


    def test_glow_radius_scales_with_resolution(self):
        """Parlama yarıçapları piksel olarak sabit kalsaydı yüksek çözünürlük daha keskin (nokta dokulu) çıkardı.
        Aynı kare 2 kat çözünürlükte çizilip küçültülünce keskinlik ilkine yakın kalmalı (ölçeksizken ~1,7 katı)."""
        def sharp(img):
            g = img.max(axis=-1)
            return (np.abs(np.diff(g, axis=1)).mean() + np.abs(np.diff(g, axis=0)).mean()) / g.mean()
        ratios = []
        for tau in (0.12, 0.3, 0.8):
            a = Renderer(dict(SPEC, width=360, height=640)).frame(tau)
            b = Renderer(dict(SPEC, width=720, height=1280)).frame(tau)
            down = b.reshape(640, 2, 360, 2, 3).mean(axis=(1, 3))
            ratios.append(float(sharp(down) / sharp(a)))
        self.assertLess(max(ratios), 1.2, f'yüksek çözünürlükte görünüm keskinleşiyor: {ratios}')


class InterestMeasure(unittest.TestCase):
    """Açılış seçimi için kullanılan ölçü, ekranda görünen parlaklıkla aynı şeyi ölçmeli."""

    def setUp(self):
        from formform import raster
        self.raster = raster
        self.light = L.LIGHTING['minimal high-contrast lighting emphasizing surface depth']
        rng = np.random.default_rng(3)
        self.lin = (rng.random((120, 80, 3)) ** 6).astype(np.float32) * 3.0
        self.lin[:40] = 0.0                                           # siyah arka plan bölgesi

    def test_screen_brightness_depends_only_on_the_brightest_channel(self):
        """Ton eşlemenin matematiği: ekrandaki en parlak kanal = (1 - exp(-p)) ** (kontrast / 1,15)."""
        for gain in (0.3, 1.0, 2.7):
            p = np.maximum(self.lin * (gain * self.light['expo']), 0).max(axis=-1)
            want = np.power(1.0 - np.exp(-p), self.light['contrast'] / 1.15)
            got = self.raster.tonemap(self.lin, gain, self.light).max(axis=-1)
            self.assertTrue(np.allclose(got, want, atol=1e-5))

    def test_histogram_measure_matches_the_direct_measure_at_any_gain(self):
        hist = self.raster.luma_hist(self.lin)
        self.assertAlmostEqual(float(hist.sum()), 1.0, places=6)
        for gain in (0.2, 0.9, 1.6, 3.0):
            lum = self.raster.tonemap(self.lin, gain, self.light).max(axis=-1)
            direct = float(lum.mean() * (0.5 + (lum > 0.15).mean()))
            self.assertAlmostEqual(self.raster.hist_interest(hist, gain, self.light) / direct, 1.0, delta=0.05)

    def test_brighter_and_fuller_frames_score_higher(self):
        hist = self.raster.luma_hist(self.lin)
        dim = self.raster.hist_interest(hist, 0.2, self.light)
        bright = self.raster.hist_interest(hist, 1.5, self.light)
        self.assertGreater(bright, dim)
        empty = self.raster.luma_hist(np.zeros((50, 50, 3), np.float32))
        self.assertLess(self.raster.hist_interest(empty, 1.0, self.light), 1e-6)       # boş kare ≈ 0


class OpeningMoment(unittest.TestCase):
    def test_peak_start_opens_on_a_full_frame_and_keeps_the_loop_closed(self):
        with tempfile.TemporaryDirectory() as d:
            info_peak = small_render(os.path.join(d, 'peak.mp4'), start='peak')
            info_base = small_render(os.path.join(d, 'base.mp4'), start='base')
            peak = gray_frames(os.path.join(d, 'peak.mp4'), 180, 320)
            base = gray_frames(os.path.join(d, 'base.mp4'), 180, 320)
        self.assertEqual(len(peak), len(base))
        self.assertEqual(info_base['start_frame'], 0)
        self.assertGreater(info_peak['start_frame'], 0)
        m_peak, m_base = peak.mean(axis=(1, 2)), base.mean(axis=(1, 2))
        # açılış karesi ortalamadan dolu ve temel formdan (başlangıç) dolu
        self.assertGreaterEqual(m_peak[0], m_peak.mean() * 0.98)
        # ekranda görünen doluluk ölçüsüne göre kareler arasında en üst çeyrekte (ilk sürümde ham kazançla ölçüldüğü
        # için gerçek parlaklıkla uyuşmuyor, açılış karesi 240 kareden 216. sıradaydı)
        screen = peak.mean(axis=(1, 2)) * (0.5 + (peak > 0.15).mean(axis=(1, 2)))
        rank = int((screen > screen[0]).sum())
        self.assertLessEqual(rank, len(screen) // 4, f'açılış karesi {len(screen)} içinde {rank + 1}. sırada')
        self.assertGreater(m_peak[0], m_base[0])
        # peak kipi, base kipinin döngüsel kaydırılmışı: parlaklık dizisi aynı
        shift = info_peak['start_frame']
        self.assertGreater(np.corrcoef(m_peak, np.roll(m_base, -shift))[0, 1], 0.99)
        # döngü kapalı: son kareden ilk kareye geçiş, tipik ardışık geçişten çok büyük değil
        steps = [np.abs(peak[i + 1] - peak[i]).mean() for i in range(len(peak) - 1)]
        seam = float(np.abs(peak[0] - peak[-1]).mean())
        self.assertLess(seam, 3.0 * float(np.max(steps)))

    def test_forward_loop_is_never_rotated(self):
        with tempfile.TemporaryDirectory() as d:
            info = small_render(os.path.join(d, 'f.mp4'), loop='forward')
        self.assertEqual(info['start_frame'], 0)

    def test_invalid_start_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize_spec(dict(SPEC, start='middle'))


class SoundMux(unittest.TestCase):
    def test_video_gets_matching_aac_audio_and_no_temp_files_remain(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, 'v.mp4')
            info = small_render(out, sound='airy pad', duration=4, fps=12)
            self.assertEqual(info['sound'], 'airy pad')
            files = sorted(os.listdir(d))
            snd = check.probe_audio(out)
            vid = check.probe(out)['streams'][0]
        self.assertEqual(files, ['v.mp4'], f'geçici dosya kaldı: {files}')
        self.assertEqual((snd['codec_name'], int(snd['channels']), int(snd['sample_rate'])), ('aac', 2, 44100))
        self.assertAlmostEqual(float(snd['duration']), float(vid['duration']), delta=0.1)

    def test_sound_listens_to_the_finished_frames(self):
        """Ses, bitmiş karelerden ölçülen eğrilerle üretilir: kare sayısı kadar ölçü, zaman çizelgesi sırasında."""
        from unittest import mock
        from formform import audio
        seen = {}

        def spy(plan, style, n, start_fraction=0.0, visual=None):
            seen.update(visual=visual, start_fraction=start_fraction)
            return audio_synthesize(plan, style, n, start_fraction=start_fraction, visual=visual)

        audio_synthesize = audio.synthesize
        with tempfile.TemporaryDirectory() as d, mock.patch.object(audio, 'synthesize', spy):
            out = os.path.join(d, 'v.mp4')
            info = small_render(out, sound='deep drone', duration=4, fps=12)
            level = check.mean_volume_db(out)
        frames = 4 * 12
        v = seen['visual']
        self.assertEqual(sorted(v), ['glint', 'mass', 'motion', 'pan'])
        for key in v:
            self.assertEqual(v[key].shape, (frames,), key)
            self.assertTrue(np.isfinite(v[key]).all(), key)
        self.assertGreater(float(v['motion'].max()), 0.0)
        self.assertAlmostEqual(seen['start_fraction'], info['start_frame'] / frames)
        # açılış karesi en dolu karedir: zaman çizelgesi sırasına çevrilmiş doluluk eğrisinde o kare öndedir
        self.assertGreaterEqual(float(v['mass'][info['start_frame']]), float(np.median(v['mass'])))
        self.assertAlmostEqual(info['loudness_lufs'], audio.TARGET_LUFS, delta=1.0)
        self.assertGreater(level, -30.0)

    def test_silent_spec_reports_no_loudness(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(small_render(os.path.join(d, 'v.mp4'))['loudness_lufs'])

    def test_silent_spec_has_no_audio_stream(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, 'v.mp4')
            small_render(out)
            self.assertIsNone(check.probe_audio(out))

    def test_forward_loop_with_sound_also_works(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, 'v.mp4')
            small_render(out, loop='forward', sound='sub pulse')
            self.assertIsNotNone(check.probe_audio(out))

    def test_audio_follows_the_rotation(self):
        """Video döngüde kaydırılıp başladığı için ses de aynı yerden başlar (görüntü-ses senkronu)."""
        sys.path.insert(0, os.path.join(ROOT, 'render'))
        from formform import audio
        from formform.timeline import Plan
        with tempfile.TemporaryDirectory() as d:
            info = small_render(os.path.join(d, 'a.mp4'), sound='glass harmonics', duration=4, fps=12)
        s = normalize_spec(dict(SPEC, sound='glass harmonics', duration=4, fps=12))
        n = audio.n_samples(4, 12)
        base = audio.synthesize(Plan(s), 'glass harmonics', n)
        frames = int(round(4 * 12))
        want = audio.synthesize(Plan(s), 'glass harmonics', n, start_fraction=info['start_frame'] / frames)
        shift = int(round(info['start_frame'] / frames * n))
        self.assertTrue(np.array_equal(want, np.roll(base, -shift, axis=0)))


class MuxSafety(unittest.TestCase):
    def test_audio_longer_than_the_picture_never_stretches_the_video(self):
        """Ses kaydı resimden uzun olsa bile çıktı resmin süresinde biter (-shortest emniyeti)."""
        from formform import audio
        with tempfile.TemporaryDirectory() as d:
            video, wav, out = (os.path.join(d, n) for n in ('v.mp4', 'a.wav', 'o.mp4'))
            make_clip(video, audio=None, v_dur=3)
            audio.write_wav(wav, np.zeros((audio.SR * 6, 2), np.float32))
            engine._mux_audio(video, wav, out)
            v, a = check.probe(out)['streams'][0], check.probe_audio(out)
        self.assertAlmostEqual(float(a['duration']), float(v['duration']), delta=0.15)
        self.assertLess(float(a['duration']), 3.3)


class QualityGate(unittest.TestCase):
    @staticmethod
    def stats(luma, lit, thumbs):
        return [(luma, lit, t) for t in thumbs]

    def test_healthy_video_passes(self):
        rng = np.random.default_rng(1)
        thumbs = [rng.random((20, 12)).astype(np.float32) * 0.3 for _ in range(10)]
        rep, bad = quality_report(self.stats(0.15, 0.3, thumbs))
        self.assertEqual(bad, [])
        self.assertGreater(rep['motion_mean'], 0.01)

    def test_black_empty_and_frozen_videos_are_caught(self):
        still = [np.full((20, 12), 0.2, np.float32)] * 10
        _, bad = quality_report(self.stats(0.001, 0.3, still + []))
        self.assertTrue(any('karanlık' in b for b in bad))
        self.assertTrue(any('hareketsiz' in b for b in bad))
        _, bad = quality_report(self.stats(0.15, 0.0, still))
        self.assertTrue(any('boş' in b for b in bad))

    def test_thresholds_are_far_below_real_videos(self):
        """Gerçek videolarda en düşük ölçülen: parlaklık 0,064, aydınlık alan 0,139, hareket 0,034."""
        self.assertLess(engine.QUALITY_MIN_LUMA * 3, 0.064)
        self.assertLess(engine.QUALITY_MIN_LIT * 4, 0.139)
        self.assertLess(engine.QUALITY_MIN_MOTION * 8, 0.034)

    def test_real_render_reports_quality(self):
        with tempfile.TemporaryDirectory() as d:
            info = small_render(os.path.join(d, 'q.mp4'))
        q = info['quality']
        self.assertGreater(q['luma_mean'], 0.05)
        self.assertGreater(q['lit_mean'], 0.1)
        self.assertGreater(q['motion_mean'], 0.01)


def make_clip(path, audio='sine', a_dur=3, v_dur=3, size='720x1280', rate=24):
    cmd = ['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'testsrc=size={size}:rate={rate}:duration={v_dur}']
    if audio:
        src = 'sine=frequency=440:sample_rate=44100' if audio == 'sine' else 'anullsrc=r=44100:cl=stereo'
        cmd += ['-f', 'lavfi', '-i', f'{src}:duration={a_dur}', '-af', 'aformat=channel_layouts=stereo']
    # gürültü eklenir: aksi halde sahte test görüntüsü çok iyi sıkışır ve check.py'nin "dosya çok küçük" kuralına takılır
    cmd += ['-vf', 'noise=alls=40:allf=t+u', '-c:v', 'libx264', '-pix_fmt', 'yuv420p']
    if audio:
        cmd += ['-c:a', 'aac', '-ar', '44100', '-ac', '2']
    subprocess.run(cmd + [path], check=True)


class CheckTool(unittest.TestCase):
    def check(self, **kw):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'c.mp4')
            req = kw.pop('require', False)
            make_clip(p, **kw)
            return check.problems(p, require_audio=req)

    def test_valid_video_with_audio_passes(self):
        bad, summary = self.check(require=True)
        self.assertEqual(bad, [])
        self.assertEqual(summary['audio']['codec'], 'aac')

    def test_missing_audio_is_an_error_only_when_required(self):
        self.assertEqual(self.check(audio=None, require=False)[0], [])
        bad, _ = self.check(audio=None, require=True)
        self.assertTrue(any('ses akışı yok' in b for b in bad))

    def test_silent_audio_is_caught(self):
        bad, _ = self.check(audio='silent', require=True)
        self.assertTrue(any('sessiz' in b for b in bad), bad)

    def test_audio_video_length_mismatch_is_caught(self):
        bad, _ = self.check(a_dur=1.5, v_dur=4, require=True)
        self.assertTrue(any('uyuşmuyor' in b for b in bad), bad)

    def test_spec_flag_decides_whether_audio_is_required(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'c.mp4')
            make_clip(p, audio=None)
            for sound, code in (('airy pad', 1), (None, 0)):
                spec = os.path.join(d, 's.json')
                with open(spec, 'w') as f:
                    json.dump({'sound': sound}, f)
                r = subprocess.run([sys.executable, os.path.join(ROOT, 'render', 'check.py'), p, '--spec', spec],
                                   capture_output=True, text=True)
                self.assertEqual(r.returncode, code, r.stderr)


class Library(unittest.TestCase):
    def test_every_sound_style_is_complete(self):
        keys = {'roots', 'ratios', 'amps', 'beats', 'sub', 'noise', 'noise_amp', 'shimmer', 'decay', 'trem',
                'rt60', 'wet', 'lowpass'}
        for name, st in L.SOUNDS.items():
            self.assertTrue(keys <= set(st), f'{name}: eksik anahtar {keys - set(st)}')
            self.assertEqual(len(st['ratios']), len(st['amps']), name)
            self.assertEqual(len(st['ratios']), len(st['beats']), name)
            self.assertTrue(all(isinstance(b, int) and b >= 1 for b in st['beats']), name)
            self.assertNotIn(', ', name)


if __name__ == '__main__':
    unittest.main()
