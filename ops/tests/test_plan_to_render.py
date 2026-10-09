"""Evrim motorunun ürettiği tarif, render motoru tarafından eksiksiz anlaşılmalı (sessiz yedek seçimi olmamalı)."""
import json
import os
import random
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone

from formform import library as L

from ops import evolve
from ops.tests.refs import rand_dna, rand_video

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import numpy  # noqa: F401  (yalnız var mı diye bakılır)
    HAS_NUMPY = True
except ImportError:                                  # pragma: no cover
    HAS_NUMPY = False


def specs(n=240):
    out = []
    for seed in range(n):
        rng = random.Random(seed)
        videos = [rand_video(rng, i) for i in range(rng.choice([0, 6, 20]))]
        dna = rand_dna(rng) if rng.random() > 0.3 else []
        out.append(evolve.generate(videos, dna, rng.random, datetime(2026, 10, 2, 3, 30, tzinfo=timezone.utc)))
    return out


@unittest.skipUnless(HAS_NUMPY, 'numpy yok')
class PlanToRender(unittest.TestCase):
    def test_no_fallback_warnings_for_any_generated_spec(self):
        from formform.timeline import Plan
        formats = set()
        for result in specs():
            spec = result['release_payload']
            plan = Plan(spec)
            self.assertEqual(plan.warnings, [], spec['video_id'])
            # aşama sayısı = dönüşüm + 1 (+1 seamless dönüş), malzemeler aşama başına bir tane
            self.assertEqual(len(spec['materials']), len(spec['transformations']) + 1)
            self.assertEqual(len(set(spec['transformations'])), len(spec['transformations']))
            self.assertEqual(len(set(spec['materials'])), len(spec['materials']))
            self.assertEqual(plan.loop, 'seamless')
            self.assertIn(spec['sound'], L.SOUNDS)
            self.assertEqual((spec['width'], spec['height']), (1080, 1920))
            formats.add((spec['duration'], spec['fps'], len(spec['transformations'])))
        self.assertEqual(formats, {(8, 30, 4), (15, 30, 7), (30, 24, 10)})

    def test_payload_has_everything_publish_requires(self):
        for result in specs(60):
            p = result['release_payload']
            self.assertTrue(p['video_id'] and p['caption'] and p['metadata'] and p['file_name'])
            self.assertTrue(p['caption'].splitlines()[0])                # hook = ilk satır
            self.assertIn('#formform', p['caption'])
            self.assertLess(len(p['caption']), 2200)                     # Instagram caption sınırı
            json.dumps(p)                                                # JSON'a çevrilebilir

    def test_generated_spec_really_renders(self):
        """Gerçek bir tarif (küçük boyutta) render edilir: plan → render zinciri."""
        spec = dict(specs(1)[0]['release_payload'], duration=2, fps=12, width=180, height=320)
        with tempfile.TemporaryDirectory() as d:
            sp, out = os.path.join(d, 'release.json'), os.path.join(d, 'video.mp4')
            with open(sp, 'w', encoding='utf-8') as f:
                json.dump(spec, f, ensure_ascii=False)
            p = subprocess.run([sys.executable, 'render/render.py', '--spec', sp, '--out', out],
                               cwd=ROOT, capture_output=True, text=True, timeout=180)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertNotIn('UYARI', p.stdout + p.stderr)
            self.assertGreater(os.path.getsize(out), 1000)
            self.assertEqual(sorted(os.listdir(d)), ['release.json', 'video.mp4'])      # geçici ses dosyaları temizlendi
            # Küçük deneme videosu Reels sınırlarının (check.py) altında kalır: biçimi ffprobe ile doğrula
            probe = subprocess.check_output(
                ['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                 'stream=codec_name,width,height,nb_frames', '-of', 'json', out])
            stream = json.loads(probe)['streams'][0]
            audio = subprocess.check_output(
                ['ffprobe', '-v', 'error', '-select_streams', 'a:0', '-show_entries', 'stream=codec_name,channels',
                 '-of', 'json', out])
            self.assertEqual(json.loads(audio)['streams'][0]['codec_name'], 'aac')       # her tarifin sesi var
            self.assertEqual((stream['codec_name'], stream['width'], stream['height'], int(stream['nb_frames'])),
                             ('h264', 180, 320, 24))


if __name__ == '__main__':
    unittest.main()
