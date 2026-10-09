"""FORM/FORM prosedürel render CLI.

  python render.py --spec spec.json --out video.mp4
  python render.py --spec-env FORMFORM_SPEC --out video.mp4     (GitHub Actions: sır/enjeksiyon güvenli)

spec alanları: video_id, structure, palette, motion_quality, lighting, materials (dönüşüm sayısı + 1),
transformations (2-12); isteğe bağlı: loop (seamless | forward), seed, duration (en çok 32 sn), fps,
width, height. Ayrıntı için render/README.md.
"""
import argparse
import json
import os
import sys

# numpy/scipy iş parçacıklarını kapat: paralellik kare düzeyinde, çekirdekler çakışmasın
for _v in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ.setdefault(_v, '1')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from formform.engine import render_video   # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--spec', help='spec JSON dosyası')
    g.add_argument('--spec-env', help='spec JSON içeren ortam değişkeni adı')
    ap.add_argument('--out', required=True)
    ap.add_argument('--workers', type=int)
    ap.add_argument('--duration', type=float)
    ap.add_argument('--width', type=int)
    ap.add_argument('--height', type=int)
    a = ap.parse_args()
    raw = open(a.spec, encoding='utf-8').read() if a.spec else os.environ[a.spec_env]
    spec = json.loads(raw)
    for k in ('duration', 'width', 'height'):
        if getattr(a, k):
            spec[k] = getattr(a, k)
    render_video(spec, a.out, workers=a.workers)


if __name__ == '__main__':
    main()
