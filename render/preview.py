"""Hızlı önizleme: bir spec için seçilen anların kare şeridini PNG olarak yazar.

Kullanım:
  python preview.py --structure "central ring" --palette crimson-magenta --out onizleme.png
Boş bırakılan alanlar video_id'den (seed) rastgele seçilir.
"""
import argparse
import sys
import time
import zlib

import numpy as np
from PIL import Image

sys.path.insert(0, __file__.rsplit('/', 1)[0])
from formform import library as L          # noqa: E402
from formform.engine import Renderer       # noqa: E402


def random_spec(video_id, **fixed):
    rng = np.random.default_rng(zlib.crc32(video_id.encode()))
    pick = lambda d: sorted(d)[int(rng.integers(len(d)))]
    spec = dict(video_id=video_id,
                structure=pick(L.STRUCTURES), palette=pick(L.PALETTES), motion_quality=pick(L.MOTIONS),
                lighting=pick(L.LIGHTING),
                materials=list(rng.choice(sorted(L.MATERIALS), 5, replace=False)),
                transformations=list(rng.choice(sorted(L.TRANSFORMATIONS), 4, replace=False)))
    spec.update({k: v for k, v in fixed.items() if v})
    return spec


def strip(spec, taus, width):
    r = Renderer(dict(spec, width=width, height=width * 16 // 9 // 4 * 4))
    frames = []
    for tau in taus:
        t0 = time.time()
        frames.append((r.frame(tau) * 255 + 0.5).astype(np.uint8))
        print(f'  tau={tau:.2f} {time.time() - t0:.2f}s', file=sys.stderr)
    return np.concatenate(frames, axis=1), r


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--video-id', default='FF_PREVIEW')
    ap.add_argument('--structure'); ap.add_argument('--palette'); ap.add_argument('--lighting')
    ap.add_argument('--motion'); ap.add_argument('--width', type=int, default=360)
    ap.add_argument('--taus', default='0,0.25,0.5,0.75,1')
    ap.add_argument('--out', default='onizleme.png')
    a = ap.parse_args()
    spec = random_spec(a.video_id, structure=a.structure, palette=a.palette, lighting=a.lighting,
                       motion_quality=a.motion)
    print(spec, file=sys.stderr)
    img, r = strip(spec, [float(x) for x in a.taus.split(',')], a.width)
    Image.fromarray(img).save(a.out)
    print('yazıldı', a.out, img.shape)
