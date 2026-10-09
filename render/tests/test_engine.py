"""Render motoru garantileri: determinizm, sınırlar, saf siyah arka plan, hata toleransı."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from formform import library as L                          # noqa: E402
from formform.engine import Renderer, normalize_spec       # noqa: E402
from formform.geometry import MAX_POINTS, build_cloud      # noqa: E402
from formform.timeline import Plan                         # noqa: E402


def spec(video_id='FF_20261002_080000', **kw):
    s = dict(video_id=video_id, structure='central ring', palette='crimson-magenta',
             motion_quality='slow, deliberate and hypnotic',
             lighting='soft directional studio lighting with deep shadows',
             materials=sorted(L.MATERIALS)[:5], transformations=sorted(L.TRANSFORMATIONS)[:4],
             width=160, height=288, duration=2.0, fps=12)
    s.update(kw)
    return s


class ShadingIsFinite(unittest.TestCase):
    """Gölgeleme hiçbir koşulda NaN/inf üretmemeli: tek NaN piksel videoda parazit olur."""

    def test_normals_aligned_with_view_direction(self):
        from formform import raster
        from formform.geometry import Cloud
        rng = np.random.default_rng(1)
        n = 40000
        # görüş yönüne neredeyse paralel birim normaller: float32 ile nokta çarpımı 1'i aşabilir
        nrm = np.array([0, 0, 1], np.float32) + rng.normal(0, 1e-4, (n, 3)).astype(np.float32)
        nrm /= np.linalg.norm(nrm, axis=1, keepdims=True)
        nrm *= np.float32(1.0 + 4e-7)      # normalleştirme yuvarlaması: büyüklük 1'i birkaç ulp aşar
        pos = np.zeros((n, 3), np.float32)
        cloud = Cloud(pos, nrm, np.ones(n, np.float32), np.ones(n, np.float32), np.full(n, 0.5, np.float32),
                      np.zeros((n, 2), np.float32), np.ones(n, np.float32), np.zeros(n, np.float32))
        plan = Plan(spec())
        for mat in sorted(L.MATERIALS):
            style = dict(L.MATERIALS[mat])
            sx, sy, color, weight, z, a = raster.shade(
                cloud, style, plan.light, plan.palette, 150.0, 160, 288, plan.rig['noise_phase'], 0.0,
                plan.rig['smoke_cycles'])
            self.assertTrue(np.isfinite(color).all(), f'{mat}: renk NaN')
            self.assertTrue(np.isfinite(weight).all(), f'{mat}: ağırlık NaN')

    def test_rendered_frames_are_finite_for_all_structures(self):
        for st in sorted(L.STRUCTURES):
            r = Renderer(spec(structure=st, width=160, height=288, loop='seamless',
                              transformations=sorted(L.TRANSFORMATIONS)[:7], materials=sorted(L.MATERIALS)[:8]))
            for tau in (0.0, 0.2, 0.4, 0.6, 0.8):
                self.assertTrue(np.isfinite(r.frame(tau)).all(), f'{st} tau={tau}')


class GeometryIsContinuous(unittest.TestCase):
    """Form zamanla pürüzsüz değişmeli: hiçbir karede yapraklar ani yer değiştirmemeli.

    Eski hata: yaprak sayısı kesirli (5 → 5,00004) ve tavan alınarak tam sayıya çevriliyordu.
    Sayı bir tam sayıyı geçtiği an tüm yapraklar yeniden yerleşiyor, form bir karede "pop" ediyordu."""

    @staticmethod
    def centroids(plan, tau, sc=30.0):
        """Tam görünür (ağırlığı 1) yaprakların merkezleri. Yeni beliren yaprak alfa 0'dan yavaşça
        gelir, görsel sıçrama yapmaz, o yüzden dışarıda bırakılır."""
        knobs, style, _ = plan.state(tau)
        c = build_cloud(knobs, style, tau, plan.rig, sc)
        full = int(np.floor(knobs['sheets'] + 1e-9))
        return {int(sh): c.pos[c.sheet == sh].mean(axis=0) for sh in np.unique(c.sheet) if int(sh) < full}

    def worst_isolated_spike(self, plan, steps):
        """Bir adım, iki komşu adımın en büyüğünden kat kat büyükse süreksizliktir. Hızlı ama
        pürüzsüz değişimde komşu adımlar da büyüktür. Yalnız zamanda ARDIŞIK örnekler karşılaştırılır:
        yaprak tam görünür kümesine girip çıkıyorsa koşu kesilir."""
        runs, current = [], {}
        for tau in np.linspace(0.0, 1.0, steps):
            cen = self.centroids(plan, float(tau))
            nxt = {}
            for sh, c in cen.items():
                nxt[sh] = current.get(sh, []) + [c]
            for sh in set(current) - set(cen):
                runs.append(current[sh])
            current = nxt
        runs.extend(current.values())
        worst = 0.0
        for ser in runs:
            st = [float(np.linalg.norm(ser[i + 1] - ser[i])) for i in range(len(ser) - 1)]
            for i in range(1, len(st) - 1):
                if st[i] > 0.01:
                    worst = max(worst, st[i] / max(st[i - 1], st[i + 1], 1e-4))
        return worst

    def test_no_sheet_jumps_when_the_sheet_count_crosses_an_integer(self):
        trs = sorted(L.TRANSFORMATIONS)
        cases = [('folded ribbon core', [trs[i] for i in (2, 9, 13, 5, 0, 11, 3, 7, 15, 6)]),
                 ('layered vortex', trs[3:10]), ('radial bloom', trs[8:14])]
        for st, chain in cases:
            plan = Plan(spec(structure=st, duration=30.0, transformations=chain, materials=sorted(L.MATERIALS)[:11]))
            worst = self.worst_isolated_spike(plan, 220)
            self.assertLess(worst, 5.0, f'{st}: yapraklar ani yer değiştirdi (komşusunun {worst:.1f} katı)')


class Determinism(unittest.TestCase):
    def test_same_spec_same_frame(self):
        a = Renderer(spec()).frame(0.5)
        b = Renderer(spec()).frame(0.5)
        self.assertTrue(np.array_equal(a, b))

    def test_video_id_changes_the_video(self):
        a = Renderer(spec('FF_A')).frame(0.5)
        b = Renderer(spec('FF_B')).frame(0.5)
        self.assertFalse(np.array_equal(a, b))

    def test_explicit_seed_overrides_video_id(self):
        a = Renderer(spec('FF_A', seed=7)).frame(0.3)
        b = Renderer(spec('FF_B', seed=7)).frame(0.3)
        self.assertTrue(np.array_equal(a, b))


class FrameProperties(unittest.TestCase):
    def test_range_shape_and_pure_black_corners(self):
        img = Renderer(spec(width=180, height=320)).frame(0.4)
        self.assertEqual(img.shape, (320, 180, 3))
        self.assertTrue(np.isfinite(img).all())
        self.assertGreaterEqual(img.min(), 0.0)
        self.assertLessEqual(img.max(), 1.0)
        q = (img * 255.0 + 0.5).astype(np.uint8)       # videoya yazılan 8 bit değer
        for corner in (q[:8, :8], q[:8, -8:], q[-8:, :8], q[-8:, -8:]):
            self.assertEqual(int(corner.max()), 0, 'arka plan saf siyah olmalı')

    def test_form_is_centered_and_visible(self):
        img = Renderer(spec(width=180, height=320)).frame(0.4)
        lum = img.max(axis=-1)
        ys, xs = np.nonzero(lum > 0.08)
        self.assertGreater(len(xs), 200, 'form görünür olmalı')
        self.assertLess(abs(xs.mean() - 90), 14)
        self.assertLess(abs(ys.mean() - 160), 24)


class GeometryBounds(unittest.TestCase):
    def test_every_structure_at_every_stage_is_finite_and_bounded(self):
        for st in sorted(L.STRUCTURES):
            plan = Plan(spec(structure=st))
            for tau in (0.0, 0.25, 0.5, 0.75, 1.0):
                knobs, style, _ = plan.state(tau)
                c = build_cloud(knobs, style, tau * 6, plan.rig, 150.0)
                self.assertTrue(np.isfinite(c.pos).all(), f'{st} tau={tau}: sonlu değil')
                self.assertTrue(np.isfinite(c.nrm).all())
                self.assertLess(float(np.abs(c.pos).max()), 8.0, f'{st} tau={tau}: taştı')
                self.assertLess(len(c.pos), MAX_POINTS * 1.25)

    def test_stage_complexity_is_capped_so_long_chains_keep_the_forms_identity(self):
        from formform.timeline import CAP, deviation
        trs = sorted(L.TRANSFORMATIONS)
        for st in sorted(L.STRUCTURES):
            for n in (4, 7, 10, 12):
                plan = Plan(spec(structure=st, transformations=trs[:n], materials=sorted(L.MATERIALS)[:n + 1],
                                 loop='forward'))
                base = plan.stages[0]
                for i, stage in enumerate(plan.stages[1:], 1):
                    self.assertLessEqual(deviation(stage, base), CAP + 1e-6, f'{st} n={n} aşama {i}')
                # tavan aşamaları aynılaştırmamalı: ardışık aşamalar belirgin farklı kalır
                steps = [sum(abs(a[k] - b[k]) / (L.KNOBS[k][2] - L.KNOBS[k][1]) for k in L.KNOBS)
                         for a, b in zip(plan.stages, plan.stages[1:])]
                self.assertGreater(min(steps), 0.05, f'{st} n={n}: ardışık aşamalar neredeyse aynı')

    def test_every_transformation_keeps_knobs_in_range(self):
        for tr in sorted(L.TRANSFORMATIONS):
            plan = Plan(spec(transformations=[tr] * 4))
            for stage in plan.stages:
                for name, v in stage.items():
                    lo, hi = L.KNOBS[name][1], L.KNOBS[name][2]
                    self.assertTrue(lo - 1e-9 <= v <= hi + 1e-9, f'{tr}: {name}={v}')


class Timeline(unittest.TestCase):
    def test_stage_position_is_monotone_and_spans_all_stages(self):
        for m in L.MOTIONS:
            for loop, segs in (('forward', 4), ('seamless', 5)):
                plan = Plan(spec(motion_quality=m, loop=loop))
                self.assertEqual(plan.segments, segs)
                s = [plan.stage_position(t) for t in np.linspace(0, 1, 200)]
                self.assertAlmostEqual(s[0], 0.0, places=6)
                self.assertAlmostEqual(s[-1], float(segs), places=6)
                self.assertTrue(all(b >= a - 1e-9 for a, b in zip(s, s[1:])), m)

    def test_forward_morph_first_and_last_frames_differ(self):
        r = Renderer(spec(loop='forward'))
        self.assertFalse(np.array_equal(r.frame(0.0), r.frame(1.0)))

    def test_stage_count_follows_the_transformation_list(self):
        trs = sorted(L.TRANSFORMATIONS)
        for n in (2, 4, 7, 10, 12):
            plan = Plan(spec(transformations=trs[:n], materials=sorted(L.MATERIALS)[:n + 1], loop='forward'))
            self.assertEqual(plan.segments, n)
        # fazlası kırpılır, azı tamamlanır
        self.assertEqual(Plan(spec(transformations=trs[:16], loop='forward')).segments, 12)
        self.assertEqual(Plan(spec(transformations=trs[:1], loop='forward')).segments, 2)


class SeamlessLoop(unittest.TestCase):
    """tau = 1 anındaki kare tau = 0 ile aynı olmalı: video dikişsiz döner."""

    def test_last_state_equals_first_state(self):
        for st in sorted(L.STRUCTURES):
            plan = Plan(spec(structure=st, duration=20.0, loop='seamless',
                             transformations=sorted(L.TRANSFORMATIONS)[:7]))
            k0, s0, _ = plan.state(0.0)
            k1, s1, _ = plan.state(1.0)
            for n in k0:
                self.assertAlmostEqual(k0[n], k1[n], places=6, msg=f'{st}:{n}')
            for n in s0:
                self.assertAlmostEqual(s0[n], s1[n], places=6, msg=f'{st}:{n}')

    def test_first_and_last_rendered_frames_match(self):
        for dur in (6.0, 18.0):
            r = Renderer(spec(duration=dur, loop='seamless', transformations=sorted(L.TRANSFORMATIONS)[:7]))
            a, b = r.frame(0.0, index=0), r.frame(1.0, index=1)
            # fark yalnız dither (≈1.7/255) kadar olabilir
            self.assertLess(float(np.abs(a - b).mean()), 0.004, f'{dur} sn: loop kapanmıyor')
            self.assertLess(float(np.abs(a - b).max()), 0.03)

    def test_seam_step_is_not_larger_than_a_normal_step(self):
        r = Renderer(spec(duration=6.0, fps=12, loop='seamless'))
        taus = r.taus()
        self.assertNotIn(1.0, taus)                        # tau=1 yok, kare 0 ile aynı
        frames = [r.frame(t, index=i) for i, t in enumerate(taus)]
        steps = [float(np.abs(frames[(i + 1) % len(frames)] - frames[i]).mean()) for i in range(len(frames))]
        seam, typical = steps[-1], float(np.median(steps))
        self.assertLess(seam, max(typical * 2.5, 0.01), f'dikiş adımı {seam:.4f}, tipik {typical:.4f}')

    def test_long_chain_geometry_stays_sane(self):
        trs = sorted(L.TRANSFORMATIONS)
        for st in ('central ring', 'layered vortex', 'floating core'):
            plan = Plan(spec(structure=st, duration=30.0, transformations=trs[:10],
                             materials=sorted(L.MATERIALS)[:11]))
            for tau in np.linspace(0, 1, 25):
                knobs, style, _ = plan.state(float(tau))
                c = build_cloud(knobs, style, float(tau), plan.rig, 150.0)
                self.assertTrue(np.isfinite(c.pos).all(), f'{st} tau={tau:.2f}')
                self.assertLess(float(np.abs(c.pos).max()), 8.0)


class Robustness(unittest.TestCase):
    def test_unknown_names_fall_back_with_warning(self):
        plan = Plan(spec(structure='yok böyle yapı', palette='???', materials=['x'], transformations=['y']))
        self.assertGreaterEqual(len(plan.warnings), 4)
        Renderer(spec(structure='yok böyle yapı')).frame(0.5)   # çökmemeli

    def test_validation(self):
        with self.assertRaises(ValueError):
            normalize_spec({'video_id': 'x'})
        with self.assertRaises(ValueError):
            normalize_spec(spec(width=100000))
        s = normalize_spec(spec(duration=999, fps=999))
        self.assertLessEqual(s['duration'], 32.0)
        self.assertLessEqual(s['fps'], 60)
        with self.assertRaises(ValueError):
            normalize_spec(spec(loop='boomerang'))


if __name__ == '__main__':
    unittest.main()
