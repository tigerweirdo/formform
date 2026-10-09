"""Parametrik yaprak geometrisi.

Bir form, `KNOBS` ile tarif edilen şerit yapraklarının (ribbon sheet) demetidir.
Her yaprak P(u, v) yüzeyidir: u şerit boyunca (0..1), v şerit genişliği boyunca (-1..1).
Çıktı, ekrana izdüşürülmüş değil dünya uzayında nokta bulutudur. Gölgeleme ve
rasterleştirme `raster.py` içindedir.
"""
import numpy as np

from .library import KNOBS

MAX_SHEETS = 11
TAU = 2.0 * np.pi


def clamp_knobs(k):
    return {n: float(np.clip(k[n], KNOBS[n][1], KNOBS[n][2])) for n in KNOBS}


def smoothstep(a, b, x):
    t = np.clip((x - a) / (b - a), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def smootherstep(x):
    t = np.clip(x, 0.0, 1.0)
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)


def _rot_x(a):
    c, s = np.cos(a), np.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], dtype=np.float32)


def _rot_y(a):
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], dtype=np.float32)


def _rot_z(a):
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype=np.float32)


def _noise3(p, w, seed_phase):
    """Düşük maliyetli, pürüzsüz 3B gürültü benzeri alan (üç eksen için).

    `w` faz açısıdır ve zaman katsayıları tam sayıdır: w, 2π'nin tam katı kadar ilerlediğinde
    alan başlangıca döner. Kusursuz loop için gereklidir."""
    x, y, z = p[..., 0], p[..., 1], p[..., 2]
    sp = seed_phase
    nx = np.sin(1.7 * y + 1.3 * z + w + sp[0]) + 0.5 * np.sin(3.1 * z - 2.3 * x + w + sp[1])
    ny = np.sin(1.9 * z + 1.1 * x - w + sp[2]) + 0.5 * np.sin(2.9 * x + 2.1 * y + w + sp[3])
    nz = np.sin(1.5 * x - 1.2 * y + w + sp[4]) + 0.5 * np.sin(3.3 * y + 2.0 * z - w + sp[5])
    return np.stack([nx, ny, nz], axis=-1) / 1.5


def _poly_radius(theta, n):
    """n kenarlı düzgün çokgenin yarıçapı (kristal yapı için)."""
    seg = TAU / n
    a = np.mod(theta, seg) - seg / 2.0
    return np.cos(np.pi / n) / np.cos(a)


class Cloud:
    """Bir kare için nokta bulutu: konum, normal, alan, geometrik alfa ve renk koordinatları."""

    __slots__ = ('pos', 'nrm', 'area', 'alpha', 'hue', 'uv', 'edge_len', 'sheet')

    def __init__(self, pos, nrm, area, alpha, hue, uv, edge_len, sheet):
        self.pos, self.nrm, self.area, self.alpha = pos, nrm, area, alpha
        self.hue, self.uv, self.edge_len, self.sheet = hue, uv, edge_len, sheet


SPACING = 1.8
MAX_POINTS = 700_000


def build_cloud(k, style, tau, rig, sc):
    """`k`: geometri düğmeleri (sözlük), `style`: malzeme stili, `tau`: normalize zaman 0..1,
    `rig`: video başına sabitler, `sc`: piksel / dünya birimi (örnekleme yoğunluğu için).

    Zamana bağlı her şey `w0 = 2π·tau` ve tam sayı çevrim sayılarıyla yazılır, yani
    tau = 1 anındaki form tau = 0 ile aynıdır (kusursuz loop)."""
    w0 = TAU * tau
    k = clamp_knobs(k)
    S_vis = k['sheets']
    nS = int(np.clip(np.ceil(S_vis - 1e-6), 1, MAX_SHEETS))
    turns = k['turns']
    # örnekleme aralığı ≈ SPACING piksel: yüzey sürekli görünür, nokta sayısı sınırlı kalır
    circ = TAU * k['R'] * sc * max(1.0, turns) * 1.1
    wpx = 2.0 * k['width'] * k['R'] * sc * (1.0 + 0.5 * k['fold'])
    NV_surf = float(np.clip(wpx / SPACING, 6, 140))
    NV = int(round(NV_surf * (1.0 - style['lines']) + 12 * style['lines']))
    # az telli (ince şerit, tel) yüzeylerde u yönü sıklaştırılır: maliyet NU×NV olduğundan ucuzdur
    sp_u = max(0.85, SPACING * min(1.0, NV / 30.0))
    NU = int(np.clip(circ / sp_u, 120, 3400))
    over = (S_vis * NU * NV) / MAX_POINTS
    if over > 1.0:
        shrink = 1.0 / np.sqrt(over)
        NU, NV = max(100, int(NU * shrink)), max(6, int(NV * shrink))
    u = np.linspace(0.0, 1.0, NU, dtype=np.float32)
    v = np.linspace(-1.0, 1.0, NV, dtype=np.float32)
    U, V = np.meshgrid(u, v, indexing='ij')

    ph = rig['phases'] + rig['cycles'] * w0                  # harmonik fazları (yavaş akar)
    spin = rig['spin_phase'] + rig['spin_amp'] * np.sin(rig['spin_cycles'] * w0 + rig['spin_phi'])
    glob = _rot_z(spin) @ _rot_x(k['tilt_x']) @ _rot_y(k['tilt_y'])
    open_curve = float(np.clip(turns - 1.0, 0.0, 1.0))

    parts = []
    for i in range(nS):
        w_i = float(np.clip(S_vis - i, 0.0, 1.0))
        if w_i < 0.02:
            continue
        # Yerleşim, tam sayı nS'ye değil sürekli S_vis değerine bağlıdır. Aksi halde S_vis bir tam sayıyı
        # geçtiği an (5 → 5,00004) tüm yapraklar yeniden dağılır ve form bir karede "pop" eder.
        fi = i / max(S_vis - 1.0, 1.0)                        # yeni beliren yaprak kenardan (fi > 1) girer
        jit = rig['sheet_jit'][i % len(rig['sheet_jit'])]    # yaprak başına sabit rastgelelik
        sgn = -1.0 if i % 2 else 1.0

        theta = TAU * turns * U + TAU * k['phase'] * (i / max(S_vis, 1.0)) + sgn * k['slide'] * TAU * 0.5 * (0.4 + fi) \
            + jit[0] * 0.35
        # yarıçap profili
        base_r = k['R'] * (1.0 + k['ext'] * (i % 2)) * (1.0 - k['taper'] * fi)
        r = base_r * (1.0 - k['pitch'] * U * 0.92 * (1.0 if turns > 1.0 else 0.0))
        mod = (1.0
               + k['h1'] * np.cos(theta + ph[0])
               + k['h2'] * np.cos(2 * theta + ph[1])
               + k['h3'] * np.cos(3 * theta + ph[2])
               + k['h4'] * np.cos(4 * theta + ph[3])
               + k['h5'] * np.cos(5 * theta + ph[4]))
        poly = _poly_radius(theta + ph[5] * 0.2, rig['poly_n'])
        r = r * mod * (1.0 + k['facet'] * (poly - 1.0))

        cx = r * np.cos(theta)
        cy = r * np.sin(theta) * k['aspect'] * (1.0 + k['stretch'])
        cz = (k['lift'] * k['R'] * (0.6 * np.sin(2 * theta + ph[6]) + 0.4 * np.sin(3 * theta + ph[7]))
              + k['funnel'] * k['R'] * (U - 0.5) * 1.6
              + k['spread'] * k['R'] * (fi - 0.5) * 2.0)

        # şerit yönü: radyal ile z arasında burulan düzlem
        tau = k['twist'] * theta + jit[1] * 1.2
        er_x, er_y = np.cos(theta), np.sin(theta) * k['aspect']
        dx = np.cos(tau) * er_x
        dy = np.cos(tau) * er_y
        dz = np.sin(tau)
        wid = k['width'] * k['R'] * (1.0 + 0.30 * np.sin(2 * theta + ph[2] + jit[2]))
        wv = V * wid
        px = cx + wv * dx
        py = cy + wv * dy
        pz = cz + wv * dz
        # katlanma: şerit boyunca zikzak/dalga
        tri = (2.0 / np.pi) * np.arcsin(np.sin(k['fold_f'] * np.pi * V + theta * 0.5))
        fold = (1.0 - k['facet']) * np.sin(k['fold_f'] * np.pi * V + theta * 0.5) + k['facet'] * tri
        pz = pz + k['fold'] * wid * 0.9 * fold
        px = px + k['fold'] * wid * 0.25 * fold * dx
        py = py + k['fold'] * wid * 0.25 * fold * dy

        P = np.stack([px, py, pz], axis=-1)

        # yaprakların jiroskop eğimi
        if k['sheet_tilt'] > 1e-3:
            a_i = k['sheet_tilt'] * (fi - 0.5) * np.pi * 0.95 + jit[3] * 0.25 * k['sheet_tilt']
            b_i = k['sheet_tilt'] * (((i * 0.618) % 1.0) - 0.5) * np.pi
            P = P @ (_rot_x(a_i) @ _rot_y(b_i)).T

        # sıvılaşma
        if k['warp'] > 1e-3:
            P = P + k['warp'] * k['R'] * _noise3(P * 1.6, rig['noise_cycles'] * w0, rig['noise_phase'])

        # parçalanma: u boyunca boşluklar (kaydırma, normal hesabından sonra uygulanır)
        seg = np.floor(U * rig['frag_n'] + jit[0] * 3.0)
        seg_rand = np.sin(seg * 12.9898 + i * 78.233 + rig['noise_phase'][0]) * 0.5
        a_geo = np.full_like(U, w_i)
        if k['frag'] > 1e-3:
            gap = 0.5 + 0.5 * np.cos(TAU * (U * rig['frag_n'] + jit[1]))
            a_geo = a_geo * (1.0 - k['frag'] * (1.0 - smoothstep(0.0, 0.55, gap)))
        # açık spiralin uçları yumuşar
        if open_curve > 1e-3:
            ef = smoothstep(0.0, 0.07, U) * smoothstep(0.0, 0.07, 1.0 - U)
            a_geo = a_geo * (1.0 - open_curve + open_curve * ef)
        # şerit kenarı yumuşaklığı
        e_soft = 0.10 + 0.55 * style['soft']
        a_geo = a_geo * (1.0 - smoothstep(1.0 - e_soft, 1.0, np.abs(V)) * 0.85)

        # normaller ve alan elemanı (ızgara farkları)
        dPu = np.gradient(P, axis=0)
        dPv = np.gradient(P, axis=1)
        n = np.cross(dPu, dPv)
        area = np.linalg.norm(n, axis=-1)
        nrm = n / np.maximum(area[..., None], 1e-9)
        edge_len = np.linalg.norm(dPu, axis=-1)
        if k['frag'] > 1e-3:
            P = P.copy()
            P[..., 2] += k['frag'] * k['R'] * 0.30 * seg_rand
            P[..., 0] += k['frag'] * k['R'] * 0.06 * seg_rand * np.sin(seg)

        hue = 0.5 + 0.5 * np.sin(TAU * (U * 0.5 * turns + fi * 0.37) + rig['noise_phase'][1] + 1.3 * V)
        P = P @ glob.T
        nrm = nrm @ glob.T
        parts.append((P, nrm, area, a_geo, hue, U, V, edge_len, np.full_like(U, i)))

    core_w = k['core']
    if core_w > 0.02:
        parts.append(_core(k, style, w0, rig, glob, core_w, nS))

    def cat(idx, last=None):
        arrs = [p[idx].reshape(-1, *p[idx].shape[2:]) for p in parts]
        return np.concatenate(arrs, axis=0)

    uv = np.stack([cat(5), cat(6)], axis=-1)
    return Cloud(cat(0), cat(1), cat(2), cat(3), cat(4), uv, cat(7), cat(8))


def _core(k, style, w0, rig, glob, core_w, nS):
    """Merkez çekirdek: enlem/boylam çizgilerinden oluşan yumuşak küre."""
    NU, NV = 160, 90
    u = np.linspace(0.0, 1.0, NU)
    v = np.linspace(-1.0, 1.0, NV)
    U, V = np.meshgrid(u, v, indexing='ij')
    lon = TAU * U
    lat = V * np.pi * 0.5 * 0.98
    rad = k['R'] * 0.46 * (0.6 + 0.4 * core_w)
    bump = (1.0 + 0.16 * np.sin(3 * lon + 2 * lat + rig['phases'][3])
            + 0.10 * np.sin(5 * lat - 2 * lon + rig['phases'][5]))
    r = rad * bump
    P = np.stack([r * np.cos(lat) * np.cos(lon), r * np.cos(lat) * np.sin(lon), r * np.sin(lat)], axis=-1)
    if k['warp'] > 1e-3:
        P = P + k['warp'] * k['R'] * _noise3(P * 1.6, rig['noise_cycles'] * w0, rig['noise_phase'])
    dPu = np.gradient(P, axis=0)
    dPv = np.gradient(P, axis=1)
    n = np.cross(dPu, dPv)
    area = np.linalg.norm(n, axis=-1)
    nrm = n / np.maximum(area[..., None], 1e-9)
    # yüzeye bakan normali dışa çevir
    flip = np.sign(np.sum(nrm * P, axis=-1, keepdims=True))
    nrm = nrm * np.where(flip == 0, 1.0, flip)
    a_geo = np.full_like(U, float(np.clip(core_w, 0.0, 1.0)) * 0.9)
    hue = 0.5 + 0.5 * np.sin(lon + rig['noise_phase'][2])
    return (P @ glob.T, nrm @ glob.T, area, a_geo, hue, U, V, np.linalg.norm(dPu, axis=-1),
            np.full_like(U, nS))
