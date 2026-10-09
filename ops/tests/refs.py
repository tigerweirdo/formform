"""Random row generators for tests (fake video/DNA rows resembling store CSV shape)."""
import importlib.util
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ops import evolve  # noqa: E402


def rows(*dicts):
    return [{'json': d} for d in dicts]


def rand_video(rng, i, scored=True, published=True, base_ms=1_790_000_000_000):
    """As if read from Sheets: all values are strings."""
    fmt = rng.choice(evolve.FORMAT_NAMES + ['cloudinary_boomerang'])
    n_tr = 4 if fmt == 'seamless_8s' else 7 if fmt == 'seamless_15s' else 10
    row = {
        'video_id': f'FF_{i:04d}' if rng.random() > 0.05 else f'TEST_{i}',
        'created_at': '',
        'structure': rng.choice(evolve.STRUCTURES),
        'palette': rng.choice(evolve.PALETTES),
        'materials': ', '.join(rng.sample(evolve.MATERIALS, min(n_tr + 1, len(evolve.MATERIALS)))),
        'transformations': ', '.join(rng.sample(evolve.TRANSFORMATIONS, n_tr)),
        'motion_quality': rng.choice(evolve.MOTION_QUALITIES),
        'lighting': rng.choice(evolve.LIGHTING_STYLES),
        'loop_strategy': fmt,
        'caption': f"{rng.choice(evolve.CAPTION_OPENINGS)}\n\nform/form — x\n\n#formform #a",
        'instagram_id': f'1789{i:08d}' if rng.random() > 0.05 else '',
        'published_at': (
            __import__('datetime').datetime.fromtimestamp(
                (base_ms - rng.randint(0, 40 * 24) * 3600_000 - rng.randint(0, 3599) * 1000) / 1000,
                __import__('datetime').timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z') if published else ''),
        'views_24h': '', 'views_72h': '', 'likes': '', 'comments': '', 'shares': '', 'saves': '',
        'quality_score': '', 'performance_score': '', 'selection_mode': 'weights',
        'structure_weight': '1', 'palette_weight': '1', 'motion_weight': '1', 'lighting_weight': '1',
        'avg_watch_time': '',
    }
    if scored:
        views = rng.choice([0, 3, 40, 150, 900, 5000, 40000])
        row['views_72h'] = str(views)
        row['views_24h'] = str(int(views * 0.6))
        row['likes'] = str(int(views * rng.random() * 0.1))
        row['comments'] = str(int(views * rng.random() * 0.01))
        row['shares'] = str(int(views * rng.random() * 0.02))
        row['saves'] = str(int(views * rng.random() * 0.03))
        if rng.random() > 0.4:
            row['avg_watch_time'] = str(round(rng.uniform(0.5, 20), 2))
        if rng.random() > 0.1:
            row['performance_score'] = str(round(rng.uniform(0, 100), 2))
    return row


def rand_dna(rng):
    out = []
    dims = {'structure': evolve.STRUCTURES, 'palette': evolve.PALETTES, 'motion': evolve.MOTION_QUALITIES,
            'lighting': evolve.LIGHTING_STYLES, 'format': evolve.FORMAT_NAMES, 'material': evolve.MATERIALS,
            'transformation': evolve.TRANSFORMATIONS, 'hook': evolve.CAPTION_OPENINGS}
    for dim, values in dims.items():
        for v in values:
            if rng.random() < 0.7:
                w = rng.choice([str(round(rng.uniform(0.6, 1.8), 3)), '1', '0', '', 'abc', '-1'])
                out.append({'key': f'{dim}::{v}', 'dimension': dim, 'value': v, 'avg_score': '50',
                            'sample_count': str(rng.randint(1, 9)), 'weight': w, 'updated_at': ''})
    return out
