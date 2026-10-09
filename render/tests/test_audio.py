"""Prosedürel ses: dikişsiz döngü, ses yüksekliği, determinizm, görüntüye (zaman çizelgesi ve kareler) bağlılık."""
import os
import sys
import tempfile
import unittest
import wave

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, 'render'))
from formform import audio, library as L           # noqa: E402
from formform.engine import normalize_spec        # noqa: E402
from formform.timeline import Plan                # noqa: E402


def spec(video_id='FF_AUDIO_1', duration=6, loop='seamless', **kw):
    base = dict(video_id=video_id, structure='spiral form', palette='violet-pink',
                motion_quality='controlled but progressively more energetic',
                lighting='controlled cinematic lighting with bright internal highlights',
                materials=['smooth liquid glass', 'fine glowing threads', 'glossy molten surfaces',
                           'smoky transparent layers', 'luminous ribbon-like surfaces'],
                transformations=['twist into increasingly complex overlapping folds',
                                 'collapse into narrow luminous strands', 'liquefy into smooth flowing curves',
                                 'fragment into controlled floating planes while remaining visually connected'],
                duration=duration, fps=30, loop=loop)
    base.update(kw)
    return normalize_spec(base)


def make(style, **kw):
    s = spec(**kw)
    plan = Plan(s)
    n = audio.n_samples(s['duration'], s['fps'])
    return plan, n, audio.synthesize(plan, style, n)


def db(x):
    return 20 * np.log10(max(float(x), 1e-12))


def bump(i, center, width):
    """Döngüsel çan eğrisi (i: 0..1 konumlar)."""
    return np.exp(-0.5 * ((((i - center + 0.5) % 1.0) - 0.5) / width) ** 2)


def frames_like(frames=240):
    """Kare ölçülerinin (`audio.visual_curves`) yerine geçen yapay eğriler: iki hareket dalgası, onlardan ayrı bir
    parlama dalgası, yavaş değişen doluluk ve sağa sola salınan ışık."""
    i = np.arange(frames) / frames
    return dict(motion=0.002 + 0.03 * (bump(i, 0.25, 0.05) + 0.6 * bump(i, 0.70, 0.07)),
                glint=0.01 + 0.08 * bump(i, 0.50, 0.10),
                mass=0.30 + 0.10 * np.sin(2 * np.pi * i),
                pan=0.08 * np.sin(2 * np.pi * (2 * i + 0.1)))


def per_frame(a, frames):
    """Kare başına ses ölçüleri: seviye (mono toplam, dB), tiz bant (dB), sağ-sol denge (-1..1)."""
    a = a.astype(np.float64)
    win = 2048
    f = np.fft.rfftfreq(win, 1 / audio.SR)
    pad = np.concatenate([a[-win:], a, a[:win]])
    level, high, balance = [], [], []
    for i in range(frames):
        c = int(round((i + 0.5) / frames * len(a))) + win
        seg = pad[c - win // 2:c + win // 2]
        sp = np.abs(np.fft.rfft(seg.mean(axis=1) * np.hanning(win))) ** 2
        level.append(10 * np.log10(sp.sum() + 1e-18))
        high.append(10 * np.log10(sp[f > 1200].sum() + 1e-18))
        l2, r2 = (seg[:, 0] ** 2).sum(), (seg[:, 1] ** 2).sum()
        balance.append((r2 - l2) / (r2 + l2 + 1e-18))
    return np.array(level), np.array(high), np.array(balance)


def seam_step(a, around=3000):
    """Dikişteki adımın (son örnek → ilk örnek) çevresindeki en büyük ardışık adıma oranı. Ses artık görüntüyle
    birlikte kabarıp çekildiği için ölçü yereldir: dikiş gürültülü bir ana denk gelebilir."""
    x = a.astype(np.float64)
    d = np.abs(np.diff(x, axis=0))
    local = np.concatenate([d[:around], d[-around:]])
    return float(np.abs(x[0] - x[-1]).max()) / float(local.max())


class Basics(unittest.TestCase):
    def test_length_is_exactly_the_video_length(self):
        for duration, fps in ((8, 30), (15, 30), (30, 24), (6, 30), (2.5, 24)):
            n = audio.n_samples(duration, fps)
            self.assertEqual(n, int(round(round(duration * fps) / fps * audio.SR)))
        self.assertEqual(audio.n_samples(8, 30), 352800)
        self.assertEqual(audio.n_samples(30, 24), 1323000)

    def test_shape_dtype_and_finite(self):
        for style in L.SOUNDS:
            _, n, a = make(style)
            self.assertEqual(a.shape, (n, 2), style)
            self.assertEqual(a.dtype, np.float32)
            self.assertTrue(np.isfinite(a).all(), style)

    def test_loudness_peak_and_dc(self):
        """Her stil aynı ses yüksekliğinde çıkar (Instagram/TikTok hedefi), gerçek tepe sınırın altındadır."""
        for style in L.SOUNDS:
            for duration in (6, 15):
                _, _, a = make(style, duration=duration)
                lufs = audio.loudness(a, loop=True)
                self.assertLess(abs(lufs - audio.TARGET_LUFS), 0.5, f'{style}/{duration}: {lufs:.1f} LUFS')
                self.assertLess(abs(audio.loudness(a) - audio.TARGET_LUFS), 1.0, style)      # düz (dosya) ölçümü
                self.assertLessEqual(audio.true_peak_db(a, loop=True), audio.TRUE_PEAK_DB + 0.05, style)
                self.assertLessEqual(float(np.abs(a).max()), audio.PEAK + 1e-6, style)
                self.assertLess(abs(float(a.mean())), 1e-3, f'{style}: DC kayması')

    def test_stereo_has_width_and_survives_mono_playback(self):
        """Telefon hoparlörü çoğu zaman mono çalar: kanallar ters fazda olursa ses birbirini yok eder."""
        for style in L.SOUNDS:
            for vid in ('FF_M1', 'FF_M2', 'FF_M3', 'FF_M4'):
                _, _, a = make(style, video_id=vid)
                a = a.astype(np.float64)
                c = np.corrcoef(a[:, 0], a[:, 1])[0, 1]
                self.assertLess(c, 0.92, f'{style}/{vid}: kanallar neredeyse aynı ({c:.2f})')
                self.assertGreater(c, 0.05, f'{style}/{vid}: kanallar ters fazda ({c:.2f}), mono çalmada kaybolur')
                mono = np.sqrt((((a[:, 0] + a[:, 1]) / 2) ** 2).mean())
                stereo = np.sqrt((a ** 2).mean())
                self.assertGreater(mono / stereo, 0.55, f'{style}/{vid}: mono çalmada seviye çok düşüyor')

    def test_unknown_style_is_rejected_by_the_spec(self):
        with self.assertRaises(ValueError):
            spec(sound='no such sound')
        self.assertIsNone(spec(sound='none')['sound'])
        self.assertIsNone(spec()['sound'])
        self.assertEqual(spec(sound='airy pad')['sound'], 'airy pad')


class LoudnessMeter(unittest.TestCase):
    def test_reference_tone_reads_its_own_level(self):
        """EBU Tech 3341: iki kanalda -23 dBFS tepeli 1 kHz sinüs -23,0 LUFS okunur."""
        t = np.arange(audio.SR * 10) / audio.SR
        x = 10 ** (-23 / 20) * np.sin(2 * np.pi * 1000 * t)
        tone = np.stack([x, x], axis=1)
        self.assertAlmostEqual(audio.loudness(tone), -23.0, delta=0.1)
        self.assertAlmostEqual(audio.loudness(tone, loop=True), -23.0, delta=0.1)
        self.assertAlmostEqual(audio.loudness(tone * 2.0) - audio.loudness(tone), 6.02, delta=0.05)

    def test_low_frequencies_count_less(self):
        """K ağırlığı: aynı seviyedeki 50 Hz, 1 kHz'ten belirgin biçimde sessiz sayılır (kulak ve telefon böyle duyar)."""
        t = np.arange(audio.SR * 10) / audio.SR
        tone = lambda f: np.stack([0.1 * np.sin(2 * np.pi * f * t)] * 2, axis=1)       # noqa: E731
        self.assertLess(audio.loudness(tone(50)), audio.loudness(tone(1000)) - 2.5)

    def test_silence_and_true_peak(self):
        self.assertEqual(audio.loudness(np.zeros((audio.SR * 2, 2))), -70.0)
        t = np.arange(audio.SR) / audio.SR
        x = 0.5 * np.sin(2 * np.pi * 11025 * t + np.pi / 4)        # örnekler tepeyi kaçırır: gerçek tepe -6 dB
        self.assertLess(db(np.abs(x).max()), -8.5)
        self.assertAlmostEqual(audio.true_peak_db(np.stack([x, x], axis=1), loop=True), -6.02, delta=0.3)

    def test_loop_measure_does_not_depend_on_where_the_loop_starts(self):
        _, n, a = make('glass harmonics')
        rolled = np.roll(a, n // 3, axis=0)
        self.assertAlmostEqual(audio.loudness(a, loop=True), audio.loudness(rolled, loop=True), delta=0.05)
        self.assertAlmostEqual(audio.true_peak_db(a, loop=True), audio.true_peak_db(rolled, loop=True), delta=0.05)


class Determinism(unittest.TestCase):
    def test_same_recipe_same_sound(self):
        for style in ('deep drone', 'glass harmonics'):
            a = make(style)[2]
            b = make(style)[2]
            self.assertTrue(np.array_equal(a, b), style)

    def test_different_video_different_sound(self):
        a = make('airy pad', video_id='FF_A')[2]
        b = make('airy pad', video_id='FF_B')[2]
        self.assertFalse(np.array_equal(a, b))

    def test_different_styles_differ(self):
        sounds = [make(style)[2] for style in L.SOUNDS]
        for i in range(len(sounds)):
            for j in range(i + 1, len(sounds)):
                self.assertFalse(np.allclose(sounds[i], sounds[j]))


class SeamlessLoop(unittest.TestCase):
    def test_last_sample_flows_into_first(self):
        """Dikiş: son örnekten ilk örneğe geçiş, çevresindeki ardışık adımlardan büyük değil. Kesinti olsaydı kat
        kat büyük olurdu."""
        for style in L.SOUNDS:
            for vid in ('FF_S1', 'FF_S2', 'FF_S3'):
                _, _, a = make(style, video_id=vid)
                self.assertLess(seam_step(a), 1.5, f'{style}/{vid}: dikişte kesinti ({seam_step(a):.1f}x)')

    def test_frame_driven_sound_is_seamless_too(self):
        s = spec(duration=8)
        n = audio.n_samples(8, 30)
        for style in L.SOUNDS:
            a = audio.synthesize(Plan(s), style, n, start_fraction=0.37, visual=frames_like(240))
            self.assertLess(seam_step(a), 1.5, f'{style}: dikişte kesinti ({seam_step(a):.1f}x)')
            x = a.mean(axis=1).astype(np.float64)
            loop = np.concatenate([x, x])
            hp = loop - np.convolve(loop, np.ones(5) / 5, mode='same')
            self.assertLess(np.abs(hp[n - 3:n + 3]).max() / np.abs(hp[n - 3000:n + 3000]).std(), 8.0, style)

    def test_signal_is_periodic_in_the_spectral_sense(self):
        """İki kez art arda çalınınca dikişte genişbant tıkırtı (klik) oluşmamalı."""
        _, _, a = make('glass harmonics')
        x = a.mean(axis=1).astype(np.float64)
        loop = np.concatenate([x, x])
        hp = loop - np.convolve(loop, np.ones(5) / 5, mode='same')       # basit yüksek geçiren: kliği öne çıkarır
        seam = len(x)
        around = np.abs(hp[seam - 3:seam + 3]).max()
        typical = np.abs(hp[1000:-1000]).std()
        self.assertLess(around / typical, 8.0)

    def test_rotation_keeps_the_loop_closed(self):
        _, n, base = make('airy pad')
        s = spec()
        plan = Plan(s)
        rolled = audio.synthesize(plan, 'airy pad', n, start_fraction=0.25)
        self.assertTrue(np.array_equal(rolled, np.roll(base, -int(round(0.25 * n)), axis=0)))
        self.assertLess(seam_step(rolled), 1.5)


class FollowsTheVisual(unittest.TestCase):
    def test_controls_are_periodic_bounded_and_complete(self):
        s = spec(duration=8)
        plan = Plan(s)
        for c in (audio.controls(plan, 8.0), audio.controls(plan, 8.0, frames_like(240))):
            for key in ('cx', 'burst', 'motion', 'glint', 'mass', 'lines', 'smoke'):
                self.assertTrue(np.isfinite(c[key]).all(), key)
                self.assertGreaterEqual(float(c[key].min()), 0.0, key)
                self.assertLessEqual(float(c[key].max()), 1.0 + 1e-9, key)
            self.assertTrue(np.isfinite(c['pan']).all())
            self.assertLessEqual(float(np.abs(c['pan']).max()), 1.0 + 1e-9)
            self.assertTrue(c['peaks'], 'hiç olay anı yok')
            self.assertEqual(c['peaks'], sorted(c['peaks']))
            self.assertTrue(all(0.0 <= t < 1.0 and audio.SWELL_MIN <= w <= 1.0 for t, w in c['peaks']))
            self.assertLessEqual(len(c['peaks']), plan.segments)        # geçiş başına en çok bir olay

    def test_timbre_opens_with_complexity(self):
        """Form karmaşıklaştığında üst sesler açılır (tayf ağırlık merkezi yükselir). Doku gürültüsü hıza bağlıdır
        ve bu etkiyi gölgeler, o yüzden yalnız kısmi seslerle (gürültü, çan, cam tınısı ve yankı kapalı) ölçülür."""
        from unittest import mock
        pure = dict(L.SOUNDS['airy pad'], noise_amp=0.0, shimmer=0.0, wet=0.0, glint=0.0)
        corr = []
        for vid in ('FF_T1', 'FF_T2', 'FF_T3', 'FF_T4'):
            s = spec(video_id=vid, duration=8)
            plan = Plan(s)
            n = audio.n_samples(8, 30)
            with mock.patch.dict(L.SOUNDS, {'saf pad': pure}):
                a = audio.synthesize(plan, 'saf pad', n).mean(axis=1).astype(np.float64)
            win = 8192
            hop = 8192
            starts = np.arange(0, n - win, hop)
            f = np.fft.rfftfreq(win, 1 / audio.SR)
            cent = np.array([(f * np.abs(np.fft.rfft(a[i:i + win] * np.hanning(win)))).sum() /
                             (np.abs(np.fft.rfft(a[i:i + win] * np.hanning(win))).sum() + 1e-12) for i in starts])
            cx = audio._periodic(audio.controls(plan, n / audio.SR)['cx'], n)
            cxw = np.array([cx[i:i + win].mean() for i in starts])
            corr.append(np.corrcoef(cent, cxw)[0, 1])
        self.assertGreater(float(np.mean(corr)), 0.2, f'ses karmaşıklıkla birlikte açılmıyor: {corr}')

    def test_burst_moments_swell_the_texture(self):
        s = spec(duration=8)
        plan = Plan(s)
        c = audio.controls(plan, 8.0)
        self.assertGreater(float(c['burst'].max()), 0.5)       # hızlı değişim anları kontrol eğrisinde var


class FollowsTheFrames(unittest.TestCase):
    """Bitmiş karelerden ölçülen eğriler verildiğinde ses ekranda görüneni izler."""

    FRAMES = 240

    def setUp(self):
        self.s = spec(duration=8)
        self.n = audio.n_samples(8, 30)
        self.v = frames_like(self.FRAMES)

    def synth(self, style, **kw):
        return audio.synthesize(Plan(self.s), style, self.n, visual=self.v, **kw)

    def test_level_follows_the_motion_on_screen(self):
        """Form hareketlenince ses kabarır, durulunca çekilir. Eğriler planla ilgisizdir: ses planı değil kareyi izler."""
        for style in L.SOUNDS:
            level = per_frame(self.synth(style), self.FRAMES)[0]
            c = np.corrcoef(level, self.v['motion'])[0, 1]
            self.assertGreater(c, 0.5, f'{style}: seviye hareketi izlemiyor ({c:.2f})')

    def test_no_delay_between_picture_and_sound(self):
        """Gerçek render'daki gibi ekrandaki hareket morph'un kendi hızı olduğunda, ses seviyesi ile hareketin en iyi
        örtüşmesi sıfır gecikmededir (±1 kare): yankı kuyruğu ya da yumuşatma sesi görüntünün gerisine düşürmez.
        Nabızlı stil ölçülmez: kendi periyodik nabzı görüntüden bağımsızdır ve en iyi örtüşmeyi birkaç kare oynatır."""
        plan = Plan(self.s)
        est = audio.controls(plan, 8.0)['motion']
        at_frames = np.interp(np.arange(self.FRAMES) / self.FRAMES, np.arange(len(est)) / len(est), est, period=1.0)
        v = dict(self.v, motion=0.001 + 0.03 * at_frames ** 2)
        for style in (name for name, st in L.SOUNDS.items() if not st.get('pulse')):
            level = per_frame(audio.synthesize(plan, style, self.n, visual=v), self.FRAMES)[0]
            best = max(range(-12, 13), key=lambda k: np.corrcoef(np.roll(level, -k), v['motion'])[0, 1])
            self.assertLessEqual(abs(best), 1, f'{style}: ses görüntüden {best} kare kaymış')

    def test_shimmer_follows_the_highlights(self):
        for style in L.SOUNDS:
            high = per_frame(self.synth(style), self.FRAMES)[1]
            c = np.corrcoef(high, self.v['glint'])[0, 1]
            self.assertGreater(c, 0.5, f'{style}: tiz bant parlamayı izlemiyor ({c:.2f})')

    def test_stereo_balance_follows_the_light(self):
        for style in L.SOUNDS:
            balance = per_frame(self.synth(style), self.FRAMES)[2]
            c = np.corrcoef(balance, self.v['pan'])[0, 1]
            self.assertGreater(c, 0.5, f'{style}: denge ışığı izlemiyor ({c:.2f})')

    def test_body_fills_out_with_the_form(self):
        """Form doldukça gövde ağırlaşır (1,4 kHz altının ağırlık merkezi iner). İnce bir bağdır: stillerin
        ortalamasında aranır."""
        win = 8192
        f = np.fft.rfftfreq(win, 1 / audio.SR)
        band = (f > 30) & (f < audio.GLINT_HZ)
        corr = []
        for style in L.SOUNDS:
            a = self.synth(style).astype(np.float64)
            pad = np.concatenate([a[-win:], a, a[:win]])
            cent = []
            for i in range(self.FRAMES):
                c = int(round((i + 0.5) / self.FRAMES * self.n)) + win
                sp = np.abs(np.fft.rfft(pad[c - win // 2:c + win // 2].mean(axis=1) * np.hanning(win))) ** 2
                cent.append((sp[band] * f[band]).sum() / (sp[band].sum() + 1e-18))
            corr.append(np.corrcoef(self.v['mass'], -np.array(cent))[0, 1])
        self.assertGreater(float(np.mean(corr)), 0.25, f'gövde doluluğu izlemiyor: {np.round(corr, 2)}')

    def test_events_sit_on_the_peak_of_the_motion_on_screen(self):
        c = audio.controls(Plan(self.s), 8.0, self.v)
        m = len(c['motion'])
        self.assertTrue(c['peaks'])
        for tau, strength in c['peaks']:
            j = int(round(tau * m)) % m
            near = c['motion'][(j + np.arange(-3, 4)) % m]
            self.assertGreaterEqual(float(c['motion'][j]) + 1e-9, float(near.max()) - 0.02, f'olay doruğun dışında: {tau}')
            self.assertAlmostEqual(strength, float(c['motion'][j]), delta=0.05)
        gaps = np.diff([t for t, _ in c['peaks']] + [c['peaks'][0][0] + 1.0]) * 8.0
        self.assertGreaterEqual(float(gaps.min()), audio.PEAK_GAP - 1e-6)

    def test_same_frames_same_sound_and_different_frames_differ(self):
        a = self.synth('airy pad')
        self.assertTrue(np.array_equal(a, self.synth('airy pad')))
        other = dict(self.v, motion=self.v['motion'][::-1].copy())
        b = audio.synthesize(Plan(self.s), 'airy pad', self.n, visual=other)
        self.assertFalse(np.allclose(a, b))
        self.assertFalse(np.allclose(a, audio.synthesize(Plan(self.s), 'airy pad', self.n)))

    def test_rotation_keeps_frame_driven_sound_in_place(self):
        base = self.synth('glass harmonics')
        rolled = self.synth('glass harmonics', start_fraction=0.25)
        self.assertTrue(np.array_equal(rolled, np.roll(base, -int(round(0.25 * self.n)), axis=0)))


class FrameFeatures(unittest.TestCase):
    def test_features_of_a_frame(self):
        rng = np.random.default_rng(3)
        img = rng.random((64, 48, 3)).astype(np.float32)
        small, glint = audio.frame_features(img)
        self.assertEqual(small.shape, (64 // audio.FEATURE_BLOCK, 48 // audio.FEATURE_BLOCK))
        self.assertTrue(0.0 <= glint <= 1.0)
        self.assertEqual(audio.frame_features(np.zeros((64, 48, 3), np.float32))[1], 0.0)
        self.assertEqual(audio.frame_features(np.ones((64, 48, 3), np.float32))[1], 1.0)
        # doygun renk parlama değildir, beyaza kaçan piksel parlamadır
        red = np.zeros((64, 48, 3), np.float32)
        red[..., 0] = 1.0
        self.assertEqual(audio.frame_features(red)[1], 0.0)

    def clip(self, n=12):
        """Sağa doğru kayan parlak leke: kare ölçüleri."""
        out = []
        for i in range(n):
            img = np.zeros((64, 96, 3), np.float32)
            x = 8 + i * 6
            img[16:48, x:x + 16] = 0.9
            out.append(audio.frame_features(img))
        return out

    def test_curves_from_frames(self):
        feats = self.clip()
        v = audio.visual_curves(feats)
        for key in ('motion', 'glint', 'mass', 'pan'):
            self.assertEqual(v[key].shape, (len(feats),), key)
            self.assertTrue(np.isfinite(v[key]).all(), key)
        self.assertGreater(float(v['motion'].min()), 0.0)             # leke her karede kayıyor
        self.assertLess(float(v['pan'][0]), 0.0)                       # solda başlar
        self.assertGreater(float(v['pan'][-1]), 0.0)                   # sağda biter
        self.assertTrue(np.all(np.diff(v['pan']) > 0))
        still = audio.visual_curves([feats[0]] * 6)
        self.assertEqual(float(still['motion'].max()), 0.0)

    def test_curves_are_returned_in_timeline_order(self):
        """Video döngüde `start` karesinden başlar: çıktı sırasındaki ölçüler zaman çizelgesi sırasına çevrilir."""
        feats = self.clip()
        start = 5
        shown = feats[start:] + feats[:start]                          # çıktıdaki sıra
        a, b = audio.visual_curves(feats, 0), audio.visual_curves(shown, start)
        for key in a:
            self.assertTrue(np.allclose(a[key], b[key]), key)

    def test_forward_mode_has_no_wrap_around_motion(self):
        """İleri kipte ilk kare son karenin devamı değildir: aradaki sıçrama hareket sayılmaz."""
        feats = self.clip()
        loop, forward = (audio.visual_curves(feats, seamless=x)['motion'] for x in (True, False))
        self.assertGreater(loop[0], 2 * forward[0])
        self.assertEqual(forward[0], forward[1])
        self.assertTrue(np.array_equal(loop[1:], forward[1:]))


class Wav(unittest.TestCase):
    def test_roundtrip_16bit_stereo(self):
        _, n, a = make('deep drone', duration=3)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'a.wav')
            audio.write_wav(path, a, seed=7)
            with wave.open(path, 'rb') as w:
                self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()),
                                 (2, 2, audio.SR, n))
                pcm = np.frombuffer(w.readframes(n), dtype='<i2').reshape(-1, 2) / 32767.0
        self.assertLess(float(np.abs(pcm - a).max()), 3.0 / 32767.0)       # yalnız niceleme + 1 LSB dither


if __name__ == '__main__':
    unittest.main()
