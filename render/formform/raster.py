"""Gölgeleme, izdüşüm, rasterleştirme ve görüntü sonrası işlemler.

Kare, nokta bulutunun alan ağırlıklı bilineer sıçratması (splatting) ile toplanır.
Toplama toplamsaldır: örtüşen yarı saydam yüzeyler parlar, siyah arka plan siyah kalır.
`opaque` malzemelerde ön yüzey derinlik sıralı ikinci bir katmanda çizilir.
"""
import numpy as np
from scipy.ndimage import gaussian_filter, zoom

from .geometry import smoothstep

CAM = 4.8           # kamera mesafesi (dünya birimi)
FIT_X = 0.400       # formun yatay yarıçapı (genişlik oranı)
FIT_Y = 0.350       # formun dikey yarıçapı (yükseklik oranı)
STRETCH_Y = 1.4     # dikey uzatma: 9:16 kareyi daha iyi doldurur (form yatayda sınırlı kalır, altı üstü boş kalmaz)
CENTER_Y = 0.47     # formun dikey merkezi (yükseklik oranı). Instagram arayüzü alt kenarı kapatır, biraz yukarı alınır
F32 = np.float32


def _norm(a):
    n = np.sqrt(np.einsum('...i,...i->...', a, a))[..., None]
    return a / np.maximum(n, F32(1e-9))


def _vec(x):
    return np.asarray(x, dtype=F32)


def project(pos, W, H, sc, center=(0.0, 0.0)):
    """Dünya → ekran. `center`: izdüşürülmüş birim uzayda formun merkezi, kadraj ortasına getirilir."""
    f = 1.0 / np.maximum(1.0 - pos[:, 2] / CAM, 0.15)
    sx = W * 0.5 + sc * (pos[:, 0] * f - center[0])
    sy = H * CENTER_Y - STRETCH_Y * sc * (pos[:, 1] * f - center[1])
    return sx, sy, f


def fit_scale(pos, alpha, W, H):
    """Formu sabit görsel kütlede ve kadrajın ortasında tutan ölçek (px / birim) ve merkez."""
    m = alpha > 0.15
    p = pos[m] if m.sum() > 50 else pos
    f = 1.0 / np.maximum(1.0 - p[:, 2] / CAM, 0.15)
    x, y = p[:, 0] * f, p[:, 1] * f
    x0, x1 = np.percentile(x, [0.5, 99.5])
    y0, y1 = np.percentile(y, [0.5, 99.5])
    rx = max((x1 - x0) / 2, 1e-3)
    ry = max((y1 - y0) / 2, 1e-3)
    sc = float(min(FIT_X * W / rx, FIT_Y * H / (ry * STRETCH_Y)))
    return sc, ((x0 + x1) / 2, (y0 + y1) / 2)


def shade(cloud, style, light, pal, sc, W, H, noise_phase, w0, smoke_cycles, center=(0.0, 0.0)):
    """Her nokta için renk (doğrusal RGB), ağırlık (px²·parlaklık), derinlik ve alfa döndürür."""
    pos, nrm = cloud.pos, cloud.nrm
    sx, sy, f = project(pos, W, H, sc, center)
    V = _norm(_vec([0.0, 0.0, CAM]) - pos)
    ndv_raw = np.einsum('ij,ij->i', nrm, V)
    Nf = nrm * np.where(ndv_raw < 0, F32(-1), F32(1))[:, None]
    ndv = np.clip(np.abs(ndv_raw), 0.0, 1.0)   # float32 yuvarlaması 1'i aşabilir: (1 - ndv) ** 2.4 NaN verirdi

    if style['facet_shade'] > 0.01:
        Nq = _norm(np.round(Nf * 2.2) / 2.2 + F32(1e-4))
        Nf = _norm(Nf * (1 - style['facet_shade']) + Nq * style['facet_shade'])

    L = _norm(_vec(light['key']))
    ndl = Nf @ L
    wrap = light['wrap']
    diff = np.clip((ndl + wrap) / (1.0 + wrap), 0.0, 1.0)
    fres = (1.0 - ndv) ** 2.4

    Hh = _norm(L + V)
    nh = np.clip(np.einsum('ij,ij->i', Nf, Hh), 0.0, 1.0)
    sp = max(style['spec'], 2.0)
    spec = (nh ** sp) * style['sheen'] * light['spec']
    L2 = _norm(_vec([-L[0], -L[1] * 0.3, 0.5]))
    nh2 = np.clip(np.einsum('ij,ij->i', Nf, _norm(L2 + V)), 0.0, 1.0)
    spec = spec + 0.35 * (nh2 ** (sp * 0.6)) * style['sheen'] * light['spec']

    # ortam yansıması: yumuşak ışık kutusu + yatay şerit
    if style['refl'] > 0.01:
        Rv = _norm(2.0 * np.einsum('ij,ij->i', Nf, V)[:, None] * Nf - V)
        box = np.exp(-((1.0 - Rv @ L) ** 2) * 9.0)
        band = np.clip(np.sin(Rv[:, 1] * 5.5 + 0.8 + noise_phase[3]), 0, 1) ** 6
        env = 0.95 * box + 0.45 * band + 0.06
        spec = spec + style['refl'] * env * 0.7 * light['spec']

    emit = style['emit'] + light['inner'] * (0.35 + 0.65 * ndv ** 0.6)
    body = diff * light['diffuse'] * 0.62 + emit * 0.55
    rim = fres * style['rim'] * light['rim']

    a = cloud.alpha * style['alpha']
    uu, vv = cloud.uv[:, 0], cloud.uv[:, 1]
    if style['smoke'] > 0.01:     # duman: alfa modülasyonu
        n = (0.5 + 0.5 * np.sin(11.0 * uu + 2.2 * vv + smoke_cycles[0] * w0 + noise_phase[0])) \
            * (0.5 + 0.5 * np.sin(5.0 * vv - 7.0 * uu + smoke_cycles[1] * w0 + noise_phase[2]))
        a = a * (1.0 - style['smoke'] + style['smoke'] * (0.15 + 1.6 * n * n))
    if style['streak'] > 0.01:    # ince boyuna çizgilenme; kenardan bakışta söner (moire olmasın)
        s = 0.5 + 0.5 * np.cos(vv * np.pi * 11.0 + 2.0 * np.sin(uu * 12.0 + noise_phase[1]))
        a = a * (1.0 - style['streak'] * 0.8 * s * ndv)

    mid, acc, dark = _vec(pal['mid']), _vec(pal['accent']), _vec(pal['dark'])
    h = cloud.hue[:, None]
    base = mid[None, :] * (1 - h) + acc[None, :] * h
    body_c = dark + (base - dark) * np.clip(body, 0.0, 1.35)[:, None]
    rim_c = _vec(pal['light'])[None, :] * (rim * 0.85)[:, None]
    spec_c = _vec(pal['spec'])[None, :] * (spec * 0.55)[:, None]
    depth_f = np.clip(0.80 + 0.20 * (pos[:, 2] / 1.4 + 0.5), 0.55, 1.0)[:, None]
    color = (body_c + rim_c + spec_c) * depth_f

    # alan ağırlığı: ekran uzayında kapladığı piksel alanı (kenardan bakışta çizgi tabanı)
    pix = sc * f
    area_px = cloud.area * pix * pix * ndv * STRETCH_Y      # dikey uzayan yüzey daha çok piksel kaplar
    lw = 1.45 * W / 720.0       # çizgi tabanı kadrajla orantılı: her çözünürlükte aynı görünüm
    line_px = cloud.edge_len * pix * lw
    surf = np.sqrt(area_px ** 2 + line_px ** 2)
    thread = cloud.edge_len * pix * lw
    w_area = surf * (1.0 - style['lines']) + thread * style['lines']
    weight = a * w_area
    return sx, sy, color, weight, pos[:, 2], a


def splat(sx, sy, color, weight, W, H):
    x0 = np.floor(sx).astype(np.int64)
    y0 = np.floor(sy).astype(np.int64)
    fx = sx - x0
    fy = sy - y0
    ok = (x0 >= 0) & (x0 < W - 1) & (y0 >= 0) & (y0 < H - 1) & (weight > 1e-6)
    x0, y0, fx, fy, wt, col = x0[ok], y0[ok], fx[ok], fy[ok], weight[ok], color[ok]
    out = np.zeros((3, H * W), dtype=np.float64)
    for dx, dy, bw in ((0, 0, (1 - fx) * (1 - fy)), (1, 0, fx * (1 - fy)),
                       (0, 1, (1 - fx) * fy), (1, 1, fx * fy)):
        idx = (y0 + dy) * W + (x0 + dx)
        ww = wt * bw
        for c in range(3):
            out[c] += np.bincount(idx, weights=ww * col[:, c], minlength=H * W)
    return out.reshape(3, H, W).transpose(1, 2, 0).astype(F32)


def splat_opaque(sx, sy, color, z, a, W, H):
    """Derinlik sıralı ön yüzey katmanı: uzaktan yakına yazılır, yakın olan kalır."""
    ok = a > 0.12
    sx, sy, color, z = sx[ok], sy[ok], color[ok], z[ok]
    order = np.argsort(z)
    sx, sy, color = sx[order], sy[order], color[order]
    buf = np.zeros((H * W, 3), dtype=F32)
    cov = np.zeros(H * W, dtype=F32)
    x0 = np.floor(sx).astype(np.int64)
    y0 = np.floor(sy).astype(np.int64)
    for dx in (0, 1):
        for dy in (0, 1):
            xx, yy = x0 + dx, y0 + dy
            m = (xx >= 0) & (xx < W) & (yy >= 0) & (yy < H)
            idx = yy[m] * W + xx[m]
            buf[idx] = color[m]
            cov[idx] = 1.0
    return buf.reshape(H, W, 3), cov.reshape(H, W)


def blur(img, sigma):
    if sigma < 2.5:
        return gaussian_filter(img, (sigma, sigma, 0) if img.ndim == 3 else sigma)
    f = 2 if sigma < 10 else 4
    H, W = img.shape[:2]
    if H % f or W % f:
        return gaussian_filter(img, (sigma, sigma, 0) if img.ndim == 3 else sigma)
    if img.ndim == 3:
        small = img.reshape(H // f, f, W // f, f, -1).mean(axis=(1, 3))
        small = gaussian_filter(small, (sigma / f, sigma / f, 0))
        return zoom(small, (f, f, 1), order=1)
    small = img.reshape(H // f, f, W // f, f).mean(axis=(1, 3))
    small = gaussian_filter(small, sigma / f)
    return zoom(small, (f, f), order=1)


def radiance(acc, opq, cov, style, scale=1.0):
    """Toplanan enerjiden doğrusal, pozlamasız görüntü. `scale`: analiz geçişi için küçültme oranı."""
    s = style['soft']
    base = blur(acc, 1.0 * scale)
    soft = blur(acc, 3.2 * scale)
    img = base * (1 - 0.7 * s) + soft * (0.7 * s)
    lin = img + 0.26 * blur(acc, 11.0 * scale) + 0.14 * blur(acc, 34.0 * scale)

    ow = float(np.clip(style['opaque'], 0.0, 1.0))
    if ow > 0.01:
        # normalize evrişim: delikler karanlık benek bırakmasın
        cb = blur(cov, 1.3 * scale)
        o = blur(opq, 1.3 * scale) / np.maximum(cb, 0.05)[..., None]
        c = np.clip(cb * 1.6, 0.0, 1.0)[..., None]
        lin_o = (o + blur(opq, 11.0 * scale) * 0.22) * 0.9
        m = ow * c
        lin = lin * (1.0 - m) + lin_o * m
    return lin


def exposure_stat(lin):
    """Pozlama ölçüsü: aydınlık piksellerin yüksek yüzdeliği."""
    lum = lin.max(axis=-1)
    lit = lum[lum > 0.02]
    if lit.size < 20:
        return 0.9
    return float(np.percentile(lit, 93))


def tonemap(lin, gain, light):
    """Pozlama, ton eşleme ve kontrast (dither hariç): ekranda görünen 0..1 görüntü."""
    lin = np.maximum(lin * (gain * light['expo']), 0.0)
    # renk korumalı ve kanal başı ton eşlemenin karışımı: parlamalar beyaza kaçar ama renk solmaz
    peak = lin.max(axis=-1, keepdims=True)
    tm_peak = 1.0 - np.exp(-peak)
    keep = lin * (tm_peak / np.maximum(peak, 1e-6))
    chan = 1.0 - np.exp(-lin)
    ldr = chan * 0.45 + keep * 0.55
    return np.power(ldr, light['contrast'] / 1.15)


def finish(lin, gain, light, rng):
    """Pozlama, ton eşleme, kontrast ve siyahı koruyan dither."""
    H, W = lin.shape[:2]
    ldr = tonemap(lin, gain, light)
    lum = ldr.max(axis=-1, keepdims=True)
    noise = rng.random((H, W, 1), dtype=np.float32) - 0.5
    ldr = ldr + noise * (1.7 / 255.0) * smoothstep(0.004, 0.05, lum)
    return np.clip(ldr, 0.0, 1.0)


HIST_EDGES = np.logspace(-4.0, 2.0, 97)                    # ham en parlak kanal değeri için 96 log aralık
HIST_CENTERS = np.sqrt(HIST_EDGES[:-1] * HIST_EDGES[1:])


def luma_hist(lin):
    """Karenin pikselleri için ham en parlak kanal değerlerinin normalize histogramı. Ekranda görünen parlaklık
    (ton eşleme sonrası) yalnız bu değerin ve kazancın bir fonksiyonudur: 1 - exp(-p) (sonra kontrast üssü).
    Böylece kazanç sonradan (zamanda yumuşatıldıktan sonra) bilindiğinde doğru ölçü yeniden hesaplanabilir."""
    p = np.clip(lin.max(axis=-1), HIST_EDGES[0], HIST_EDGES[-1] * 0.999)
    return np.histogram(p, bins=HIST_EDGES)[0].astype(np.float64) / p.size


def hist_interest(hist, gain, light):
    """Görsel "doluluk": ekranda görünen ortalama parlaklık x (0,5 + aydınlık alan oranı). İnce, sönük tel karelerinde
    düşük, dolu ve parlak karelerde yüksek. Video açılışını seçmekte kullanılır."""
    lum = np.power(1.0 - np.exp(-HIST_CENTERS * gain * light['expo']), light['contrast'] / 1.15)
    return float((hist * lum).sum() * (0.5 + hist[lum > 0.15].sum()))
