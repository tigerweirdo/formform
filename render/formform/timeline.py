"""Zaman çizelgesi: aşamalar, aşamalar arası geçiş ve video başına rastgele sabitler.

Aşama 1 yapının başlangıç vektörüdür. Sonraki her aşama bir dönüşümün uygulanmasıdır.
Malzeme de aşama başına bir stil vektörüdür. Kare zamanı `s` aşama konumuna çevrilir,
geometri ve stil o konumda interpolasyonla bulunur.

İki kip:
- `seamless` (varsayılan): son aşamadan yumuşakça aşama 1'e dönülür, tau = 1 anındaki kare
  tau = 0 ile aynıdır. Video kusursuz döner, boomerang gerekmez.
- `forward`: ileri doğru tek yönlü morph. Döngüye çevirme dışarıda yapılır.
"""
import zlib

import numpy as np

from .geometry import TAU, clamp_knobs, smootherstep, smoothstep
from .library import (KNOBS, LIGHTING, MATERIALS, MOTIONS, PALETTES, STRUCTURES, STYLE_KEYS,
                      TRANSFORMATIONS)

MIN_TRANSFORMS, MAX_TRANSFORMS = 2, 12
RELAX = 0.82   # her aşamada tabana doğru gevşeme: uzun zincirler uç değerlere kaçmaz, form kimliğini korur

# Karmaşıklık tavanı. Bir aşamanın tabandan toplam (normalize) sapması CAP'i aşarsa, tabana doğru orantılı
# çekilir. Aksi halde uzun zincirlerde her aşama aynı "yumak" seviyesine doyar ve form kimliğini yitirir.
# Yapının kendi karmaşıklığı <= ~2,6 olduğundan 2,0'lık sapma net ama okunaklı bir değişimdir.
TANGLE = ('twist', 'fold', 'fold_f', 'h2', 'h3', 'h4', 'h5', 'sheet_tilt', 'frag', 'warp', 'slide', 'turns',
          'ext', 'spread', 'sheets', 'facet')
CAP = 2.0


def seed_from(video_id):
    return zlib.crc32(str(video_id).encode('utf-8')) & 0xFFFFFFFF


def _lookup(table, key, fallback_index, warnings, label):
    """Bilinmeyen anahtar için kararlı bir yedek seçer ve uyarı verir (render çökmesin)."""
    if key in table:
        return table[key]
    names = sorted(table)
    warnings.append(f"{label}: bilinmeyen değer {key!r}, yedek {names[fallback_index % len(names)]!r} kullanıldı")
    return table[names[fallback_index % len(names)]]


def _apply(knobs, base, ops, mag):
    k = dict(knobs)
    for name, op, val in ops:
        if op == 'mul':
            k[name] = k[name] * (1.0 + (val - 1.0) * mag)
        else:
            k[name] = k[name] + val * mag
    k = {n: base[n] + (k[n] - base[n]) * RELAX for n in KNOBS}
    k = clamp_knobs(k)
    d = deviation(k, base)
    if d > CAP:
        k = {n: base[n] + (k[n] - base[n]) * CAP / d for n in KNOBS}
    return k


def deviation(knobs, base):
    """Aşamanın tabandan toplam normalize sapması (karmaşıklık ölçüsü)."""
    return sum(abs(knobs[n] - base[n]) / (KNOBS[n][2] - KNOBS[n][1]) for n in TANGLE)


def _cycles(speed, energy, duration):
    """Hız (rad/sn) → videoya sığan tam sayı çevrim (en az 1): periyodik, kusursuz loop için."""
    return max(1, int(round(speed * energy * duration / TAU)))


class Plan:
    """Bir videonun tüm kararları: aşama vektörleri, stil, palet, ışık, rastgele sabitler."""

    def __init__(self, spec):
        self.warnings = []
        seed = int(spec.get('seed') if spec.get('seed') is not None else seed_from(spec['video_id']))
        self.seed = seed
        rng = np.random.default_rng(seed)
        duration = float(spec.get('duration', 6.0))
        self.loop = spec.get('loop', 'seamless')

        struct = _lookup(STRUCTURES, spec['structure'], seed, self.warnings, 'structure')
        self.palette = _lookup(PALETTES, spec['palette'], seed >> 3, self.warnings, 'palette')
        self.light = dict(_lookup(LIGHTING, spec['lighting'], seed >> 5, self.warnings, 'lighting'))
        motion = _lookup(MOTIONS, spec['motion_quality'], seed >> 7, self.warnings, 'motion_quality')
        self.motion = motion

        # video başına küçük rastgelelik: aynı yapı iki kez aynı görünmez
        base = {n: KNOBS[n][0] for n in KNOBS}
        base.update(struct)
        for n in ('R', 'width', 'spread', 'lift', 'fold', 'twist', 'h2', 'h3', 'aspect'):
            base[n] = base[n] * float(rng.uniform(0.88, 1.12)) if base[n] != 0 else base[n]
        base['tilt_x'] += float(rng.uniform(-0.12, 0.12))
        base['tilt_y'] += float(rng.uniform(-0.15, 0.15))
        base = clamp_knobs(base)

        # dönüşüm zinciri: 2..12 dönüşüm, eksikse kararlı yedekle tamamlanır
        transforms = list(spec['transformations'])[:MAX_TRANSFORMS]
        while len(transforms) < MIN_TRANSFORMS:
            transforms.append(sorted(TRANSFORMATIONS)[(seed + len(transforms)) % len(TRANSFORMATIONS)])
        stages = [base]
        for tr in transforms:
            ops = _lookup(TRANSFORMATIONS, tr, seed + len(stages), self.warnings, 'transformation')
            stages.append(_apply(stages[-1], base, ops, float(rng.uniform(0.85, 1.15))))

        # malzeme: aşama başına bir tane. Eksikse verilenler döngüyle tekrarlanır
        mats = list(spec['materials'])
        if not mats:
            mats = [sorted(MATERIALS)[seed % len(MATERIALS)]]
        mats = [mats[i % len(mats)] for i in range(len(stages))]
        styles = [dict(_lookup(MATERIALS, m, seed + i, self.warnings, 'material')) for i, m in enumerate(mats)]

        if self.loop == 'seamless':      # son aşamadan başa yumuşak dönüş
            stages.append(stages[0])
            styles.append(styles[0])
        self.stages, self.styles = stages, styles
        self.segments = len(stages) - 1

        # ışık yönü hafifçe oynar
        key = np.array(self.light['key'], dtype=float) + rng.uniform(-0.12, 0.12, 3)
        self.light['key'] = tuple(key)

        energy = motion['energy']
        sign8 = rng.choice([-1.0, 1.0], 8)
        speed8 = rng.uniform(0.35, 1.25, 8)
        self.rig = dict(
            energy=energy,
            # Zamana bağlı her şey tam sayı çevrimle: tau = 1'de başlangıca döner (kusursuz loop)
            cycles=np.array([sign8[i] * _cycles(speed8[i], energy, duration) for i in range(8)],
                            dtype=np.float32),
            noise_cycles=float(_cycles(0.45, energy, duration)),
            smoke_cycles=(float(_cycles(0.9, energy, duration)), -float(_cycles(0.7, energy, duration))),
            spin_amp=float(1.6 * motion['spin'] * rng.choice([-1.0, 1.0])),
            spin_cycles=float(max(1, round(duration / 14.0))),
            spin_phi=float(rng.uniform(0, TAU)),
            spin_phase=float(rng.uniform(-0.5, 0.5)),
            phases=rng.uniform(0, TAU, 8).astype(np.float32),
            noise_phase=rng.uniform(0, TAU, 6).astype(np.float32),
            poly_n=int(rng.integers(5, 9)),
            frag_n=int(rng.integers(5, 10)),
            sheet_jit=rng.uniform(-1, 1, (12, 4)).astype(np.float32),
        )
        # kısa hızlı değişim anları: her ~9 saniyede bir, rastgele konumda
        nb = max(1, int(round(duration / 9.0)))
        self.burst_at = [0.15 + 0.7 * (j + float(rng.uniform(0.25, 0.75))) / nb for j in range(nb)]

    # -- zaman -------------------------------------------------------------
    def stage_position(self, tau):
        """Normalize zaman τ ∈ [0,1] → aşama konumu s ∈ [0, segments] (monoton)."""
        m = self.motion
        beta = min(0.45, 0.38 * m['burst'])
        bump = sum(smoothstep(b - 0.07, b + 0.07, tau) for b in self.burst_at) / len(self.burst_at)
        smooth = (1.0 - beta) * (tau ** m['accel']) + beta * bump
        return self.segments * float(np.clip(smooth, 0.0, 1.0))

    def _ease(self, f):
        dw = 0.5 * self.motion['dwell']
        return float(smootherstep((f - dw * 0.5) / max(1.0 - dw, 1e-3)))

    def state(self, tau):
        """τ anındaki geometri düğmeleri ve malzeme stili."""
        s = self.stage_position(tau)
        i = int(min(np.floor(s), self.segments - 1))
        f = self._ease(s - i)
        a, b = self.stages[i], self.stages[i + 1]
        knobs = {n: a[n] * (1 - f) + b[n] * f for n in KNOBS}
        sa, sb = self.styles[i], self.styles[i + 1]
        style = {n: sa[n] * (1 - f) + sb[n] * f for n in STYLE_KEYS}
        return clamp_knobs(knobs), style, s
