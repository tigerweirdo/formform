"""Prosedürel ambient ses: telifsiz, ücretsiz, tamamen kodla üretilir.

Görüntüyle aynı ilkeler:
- **Dikişsiz döngü.** Her salınım videoya tam sayı çevrim sığacak frekansa oturtulur, gürültü ve yankı FFT ile
  (dairesel evrişim) yapılır. Yani son örnek ilk örneğe pürüzsüz bağlanır, video döndükçe ses de kopmadan döner.
- **Görüntüye bağlı.** Ses iki kaynaktan beslenir, ikisi de görüntünün kendisidir:
  1. Zaman çizelgesi (`Plan`): karmaşıklık, aşama geçişleri, en hızlı değişim anları, formun harmonik fazları.
  2. Bitmiş kareler (`frame_features`): hareket, parlama, doluluk, ışığın sağ-sol dengesi. Ekranda ne görünüyorsa
     ses onu izler: form durulunca ses çekilir, hızlanınca kabarır, parlama anında cam tınısı açılır.
  Kare ölçüsü verilmezse (`visual=None`) aynı eğriler zaman çizelgesinden kestirilir.
- **Aynı tarif, aynı ses.** Rastgelelik tohumdan gelir (`plan.seed`).

Stiller `library.SOUNDS` içindedir. Çıktı: (N, 2) float32, 44,1 kHz stereo, -14 LUFS.
"""
import wave

import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.signal import resample_poly

from . import library as L
from .geometry import TAU, smoothstep
from .library import KNOBS
from .timeline import CAP, deviation

SR = 44100
CONTROL_HZ = 50          # görüntü kontrol eğrilerinin örnekleme hızı
TARGET_LUFS = -14.0      # Instagram ve TikTok'un oynatma seviyesi (BS.1770 bütünleşik ses yüksekliği)
TRUE_PEAK_DB = -1.5      # gerçek tepe sınırı. AAC kodlaması tepeleri biraz yükseltir: dosyada -1 dBTP altında kalır
PEAK = 0.89              # örnek tepe sınırı (yaklaşık -1 dBFS)
MAX_DRIVE = 2.0          # yumuşak sınırlayıcıya en çok bu kadar yüklenilir (tepe / PEAK): hedef uğruna ses ezilmez
PENTATONIC = (1.0, 9 / 8, 5 / 4, 3 / 2, 5 / 3)

# Görüntü → ses bağlarının gücü (0 = bağ yok). Sesin görüntüyü ne kadar izleyeceği buradan ayarlanır.
FOLLOW_MOTION = 0.55     # hareket → genel seviye: form durulunca ses bu oranda çekilir (0,55 ≈ 7 dB)
LEVEL_HOLD = 0.90        # sesin kendi seviye dalgalanmasının (detune vuruşları, açılan sesler) düzlenme oranı
LEVEL_WINDOW = 0.15      # bu düzlemenin penceresi (sn): bundan hızlı pırıltıya dokunmaz
UPPER_FOLLOW = 0.65      # hareket → üst kısmi seslerin seviyesi
OPEN_BY_MOTION = 0.60    # hareket → üst seslerin açılması (karmaşıklığa eklenir)
NOISE_FLOOR = 0.20       # hareketsizken nefes/doku gürültüsünün kalan oranı
BODY_FOLLOW = 0.50       # doluluk → alt kısmi sesler ve alt bas
THREAD_THIN = 0.45       # ince tel malzemede gövdenin incelme oranı
SMOKE_BREATH = 0.80      # duman malzemede nefes gürültüsünün artışı
PAN_DEPTH = 0.30         # ışığın sağ-sol dengesi → stereo denge (0,30 ≈ ±2,7 dB)
GLINT_LEVEL = 0.16       # parlama → cam tınısının seviyesi (yatağa oranla)
GLINT_FOLLOW = 0.65      # parlama → parlak (GLINT_HZ üstü) kısmi seslerin ve tiz gürültünün seviyesi
BRIGHT_MOTION = 0.25     # hareket → aynı parlak seslerin seviyesi (az: onları asıl parlama yönetir)
GLINT_HZ = 1400.0        # "parlak" sınırı: üstündeki sesler parlamayı izler; cam tınısı bunun üstündeki ilk oktavdadır
BELL_LEVEL = 0.22        # en hızlı değişim anındaki cam/çan notasının seviyesi (yatağa oranla, stilin shimmer'ı ile çarpılır)
SWELL_LEVEL = 0.45       # aynı andaki alt kabarmanın seviyesi (yatağa oranla)
SWELL_RISE, SWELL_FALL = 0.55, 0.70      # kabarmanın yükselme ve sönme süresi (sn); tepe tam o karededir
SWELL_MIN = 0.35         # hareket eğrisinde (0..1) bundan zayıf doruklar kabarma ve nota üretmez
PEAK_SEARCH = 0.25       # olay anı, planın en hızlı anının bu kadar (sn) çevresinde ekrandaki hareketin doruğudur
PEAK_GAP = 0.35          # iki olay arasında en az bu kadar süre (sn) olur
BELL_ATTACK = 0.012      # cam/çan notasının atağı (sn)

MOTION_SMOOTH = 0.07     # kare ölçülerinin yumuşatması (sn): titreme gider, senkron bozulmaz (simetrik, gecikmesiz)
GLINT_SMOOTH = 0.05
SLOW_SMOOTH = 0.22       # doluluk ve denge yavaş izlenir
FEATURE_BLOCK = 16       # kare ölçüsü için blok ortalaması (piksel)
GLINT_LO, GLINT_HI = 0.30, 0.80          # en sönük kanal bu aralığı geçtikçe piksel "parlama" sayılır (beyaza kaçış)


def n_samples(duration, fps):
    """Videonun tam uzunluğu (kare sayısı / fps) kadar örnek."""
    frames = int(round(duration * fps))
    return int(round(frames / fps * SR))


def _periodic(curve, n):
    """Kontrol hızındaki periyodik eğriyi ses hızına taşır."""
    m = len(curve)
    return np.interp(np.arange(n) / n, np.arange(m) / m, curve, period=1.0)


def _smooth(curve, seconds):
    return gaussian_filter1d(curve, max(seconds * CONTROL_HZ, 1e-3), mode='wrap')


def _spread(x, floor=0.35):
    """Eğriyi videonun kendi aralığına yayar (0..1). Aralık ortancaya oranla çok darsa (neredeyse sabit eğri)
    gürültüyü büyütmez, eğri 0,5 çevresinde kalır."""
    lo, hi = np.percentile(x, [8, 96])
    span = max(hi - lo, floor * max(abs(float(np.median(x))), 1e-9))
    return np.clip(0.5 + (x - 0.5 * (lo + hi)) / span, 0.0, 1.0)


# ---- bitmiş kareden ölçü ------------------------------------------------------
def frame_features(img):
    """Bitmiş karede (H, W, 3; 0..1) sesin izlediği ölçüler: blok ortalamalı küçük parlaklık görüntüsü ve
    parlama alanı. Ton eşleme parlamaları beyaza kaçırdığı için parlama, en sönük kanalın yükseldiği yerdir."""
    lum = img.max(axis=-1)
    b = FEATURE_BLOCK
    h, w = max(lum.shape[0] // b, 1), max(lum.shape[1] // b, 1)
    small = lum[:h * b, :w * b].reshape(h, b, w, b).mean(axis=(1, 3)).astype(np.float32)
    glint = float(smoothstep(GLINT_LO, GLINT_HI, img.min(axis=-1)).mean())
    return small, glint


def visual_curves(features, start=0, seamless=True):
    """Kare ölçülerinden (çıktı sırasında, `frame_features`) kare başına eğriler, zaman çizelgesi sırasında.

    `start`: çıktının döngüde başladığı kaynak kare. motion: ardışık kareler arası değişim, glint: parlama alanı,
    mass: aydınlık alan (doluluk), pan: ışığın yatay ağırlık merkezi (-1 sol .. 1 sağ)."""
    small = np.stack([f[0] for f in features]).astype(np.float64)
    glint = np.array([f[1] for f in features], dtype=np.float64)
    motion = np.abs(small - np.roll(small, 1, axis=0)).mean(axis=(1, 2))
    if not seamless and len(motion) > 1:
        motion[0] = motion[1]                              # ileri kipte ilk kare son karenin devamı değildir
    mass = (small > 0.15).mean(axis=(1, 2))
    x = np.linspace(-1.0, 1.0, small.shape[2])
    w = small ** 2
    pan = (w * x).sum(axis=(1, 2)) / (w.sum(axis=(1, 2)) + 1e-9)
    curves = dict(motion=motion, glint=glint, mass=mass, pan=pan)
    return {k: np.roll(v, int(start)) for k, v in curves.items()}


def _from_frames(curve, m, offset):
    """Kare başına eğriyi kontrol hızına taşır. Kare i ekranda [i, i+1)/kare sayısı aralığındadır: anlık ölçüler
    aralığın ortasına (`offset` 0,5), bir önceki kareye göre fark ise karenin göründüğü ana (0) yazılır."""
    nf = len(curve)
    return np.interp(np.arange(m) / m, (np.arange(nf) + offset) / nf, curve, period=1.0)


# ---- kontrol eğrileri ---------------------------------------------------------
def controls(plan, duration, visual=None):
    """Görüntüden ses kontrol eğrileri (hepsi periyodik). `visual`: `visual_curves` çıktısı ya da None.

    cx karmaşıklık, burst hızlı değişim, motion ekrandaki hareket, glint parlama, mass doluluk (hepsi 0..1),
    pan ışık dengesi (-1..1), lines/smoke malzeme; peaks olay anları: her geçişin en hızlı anı (0..1) ve gücü."""
    m = max(32, int(duration * CONTROL_HZ))
    taus = np.arange(m) / m
    base = plan.stages[0]
    names = list(KNOBS)
    cx = np.empty(m)
    s = np.empty(m)
    vec = np.empty((m, len(names)))
    sty = {k: np.empty(m) for k in ('lines', 'smoke', 'alpha', 'opaque', 'sheen', 'refl')}
    for j, tau in enumerate(taus):
        knobs, style, pos = plan.state(float(tau))
        cx[j] = min(deviation(knobs, base) / CAP, 1.2) / 1.2
        s[j] = pos
        vec[j] = [(knobs[n] - KNOBS[n][1]) / (KNOBS[n][2] - KNOBS[n][1]) for n in names]
        for k in sty:
            sty[k][j] = style[k]
    seg = float(plan.segments)

    # morph hızı: düğmelerin (normalize) toplam yol alışı. Aşama içi yumuşatma ve bekleme dahildir.
    vel = np.abs(np.roll(vec, -1, axis=0) - vec).sum(axis=1) * m
    if plan.loop != 'seamless':
        vel[-1] = vel[-2]                                  # ileri kipte son adım başa sıçramadır, morph değil
    vel = _smooth(vel, 0.05)

    burst = np.zeros(m)
    for b in plan.burst_at:
        d = np.abs(((taus - b + 0.5) % 1.0) - 0.5)       # dairesel uzaklık
        burst += np.exp(-0.5 * (d / 0.035) ** 2)
    burst = np.clip(burst, 0.0, 1.0)

    cx = _smooth(cx, 0.15)

    # her geçişin (morph) en hızlı anı: olayların (nota, alt kabarma) aday anları
    fastest = []
    for k in range(int(seg)):
        idx = np.flatnonzero(np.floor(np.minimum(s, seg - 1e-9)) == k)
        if len(idx) >= 3:
            fastest.append(int(idx[np.argmax(vel[idx])]))

    rig = plan.rig
    if visual is not None:
        motion = _spread(_smooth(np.sqrt(_from_frames(visual['motion'], m, 0.0)), MOTION_SMOOTH))
        glint = _spread(_smooth(_from_frames(visual['glint'], m, 0.5), GLINT_SMOOTH))
        mass = _spread(_smooth(_from_frames(visual['mass'], m, 0.5), SLOW_SMOOTH))
        lean = _smooth(_from_frames(visual['pan'], m, 0.5), SLOW_SMOOTH)
        pan = np.clip((lean - np.median(lean)) / max(2.0 * float(lean.std()), 0.02), -1.0, 1.0)
    else:
        # Kare yokken aynı eğriler zaman çizelgesinden kestirilir: morph hızı + form dönüşü, malzemenin parlaklığı
        # ve yoğunluğu, dönüş açısı.
        swing = rig['spin_cycles'] * TAU * taus + rig['spin_phi']
        turn = np.abs(rig['spin_amp'] * rig['spin_cycles'] * TAU * np.cos(swing))
        motion = _spread(np.sqrt(vel + 0.8 * turn))
        glint = _spread(_smooth(sty['sheen'] * (1.0 - 0.6 * sty['lines']) + sty['refl'], GLINT_SMOOTH))
        mass = _spread(_smooth(sty['alpha'] * (1.0 - 0.7 * sty['lines']) * (0.5 + sty['opaque']), SLOW_SMOOTH))
        pan = np.sign(rig['spin_amp']) * np.sin(swing)

    # Olay tam ekrandaki hareketin doruğuna oturur: adayın çevresinde hareket eğrisinin tepesi aranır, olayın
    # gücü de o andaki harekettir. Zayıf ve birbirine çok yakın olanlar elenir.
    half = max(1, int(PEAK_SEARCH * CONTROL_HZ))
    found = []
    for j in fastest:
        idx = (j + np.arange(-half, half + 1)) % m
        jj = int(idx[np.argmax(motion[idx])])
        a, b, c = motion[jj - 1], motion[jj], motion[(jj + 1) % m]
        bend = a - 2 * b + c
        frac = float(np.clip(0.5 * (a - c) / bend, -0.5, 0.5)) if bend < -1e-12 else 0.0     # parabol tepe düzeltmesi
        found.append((((jj + frac) / m) % 1.0, float(b)))
    peaks = []
    gap = PEAK_GAP / duration
    for tau, strength in sorted(found, key=lambda e: -e[1]):
        if strength >= SWELL_MIN and all(abs(((tau - q + 0.5) % 1.0) - 0.5) >= gap for q, _ in peaks):
            peaks.append((tau, strength))
    peaks.sort()

    return dict(cx=cx, burst=burst, motion=motion, glint=glint, mass=mass, pan=pan,
                lines=_smooth(sty['lines'], 0.15), smoke=_smooth(sty['smoke'], 0.15), peaks=peaks)


# ---- ses yüksekliği -----------------------------------------------------------
def _k_response(n):
    """BS.1770 K ağırlığının (yüksek raf + alt kesim) frekans tepkisi, n örneklik FFT için."""
    z1 = np.exp(-1j * TAU * np.fft.rfftfreq(n))
    z2 = z1 * z1

    def biquad(b, a):
        return (b[0] + b[1] * z1 + b[2] * z2) / (a[0] + a[1] * z1 + a[2] * z2)

    A, w = 10 ** (4.0 / 40.0), TAU * 1500.0 / SR
    al, co, sq = np.sin(w) / (2.0 * np.sqrt(0.5)), np.cos(w), np.sqrt(A)
    shelf = biquad((A * ((A + 1) + (A - 1) * co + 2 * sq * al), -2 * A * ((A - 1) + (A + 1) * co),
                    A * ((A + 1) + (A - 1) * co - 2 * sq * al)),
                   ((A + 1) - (A - 1) * co + 2 * sq * al, 2 * ((A - 1) - (A + 1) * co),
                    (A + 1) - (A - 1) * co - 2 * sq * al))
    w = TAU * 38.0 / SR
    al, co = np.sin(w) / (2.0 * 0.5), np.cos(w)
    high = biquad(((1 + co) / 2, -(1 + co), (1 + co) / 2), (1 + al, -2 * co, 1 - al))
    return shelf * high


def loudness(audio, loop=False):
    """Bütünleşik ses yüksekliği (LUFS, BS.1770: K ağırlığı, 400 ms bloklar, mutlak ve göreli kapı). (N, 2) dizi.
    `loop`: bloklar dosya sonundan başa sarar. Döngü olarak çalınan sesin ölçüsüdür ve döngünün nereden
    başladığına bağlı değildir (düz ölçümde baştaki ve sondaki 300 ms daha az sayılır)."""
    x = np.asarray(audio, dtype=np.float64)
    n = len(x)
    H = _k_response(n)
    y = np.stack([np.fft.irfft(np.fft.rfft(x[:, ch]) * H, n) for ch in (0, 1)], axis=1)
    win, hop = int(0.4 * SR), int(0.1 * SR)
    if n < win:
        z = np.array([(y ** 2).mean(axis=0).sum()])
    else:
        e = y ** 2
        if loop:
            e = np.concatenate([e, e[:win]])
        acc = np.concatenate([np.zeros((1, 2)), np.cumsum(e, axis=0)])
        starts = np.arange(0, n if loop else n - win + 1, hop)     # döngüde her blok bir kez: n'de başlayan, 0'dakinin aynısıdır
        z = ((acc[starts + win] - acc[starts]) / win).sum(axis=1)

    def lk(v):
        return -0.691 + 10.0 * np.log10(np.maximum(v, 1e-12))

    z = z[lk(z) > -70.0]
    if not len(z):
        return -70.0
    z = z[lk(z) > lk(z.mean()) - 10.0]
    return float(lk(z.mean()))


def true_peak_db(audio, loop=False):
    """Gerçek tepe (dBTP): 4 kat aşırı örneklemeyle örnekler arası tepeler de sayılır. `loop`: ses döngü sayılır,
    dosyanın başı ve sonu sessizliğe açılan bir basamak gibi ölçülmez."""
    x = np.asarray(audio, dtype=np.float64)
    pad = min(64, len(x)) if loop else 0
    if pad:
        x = np.concatenate([x[-pad:], x, x[:pad]])
    up = resample_poly(x, 4, 1, axis=0)
    if pad:
        up = up[4 * pad:-4 * pad]
    return float(20.0 * np.log10(max(float(np.abs(up).max()), 1e-12)))


def _master(x):
    """Seviye: hedef ses yüksekliğine getirir, tepeleri yumuşak sınırlar, gerçek tepeyi sınırın altında tutar.
    Yalnız sabit kazanç ve örnek başına sınırlama kullanır: döngü dikişi ve görüntüyle senkron bozulmaz."""
    top = float(np.abs(x).max()) + 1e-12
    gain = 10.0 ** ((TARGET_LUFS - loudness(x, loop=True)) / 20.0)
    for _ in range(4):
        gain = min(gain, MAX_DRIVE * PEAK / top)
        y = PEAK * np.tanh(x * gain / PEAK)
        gain *= 10.0 ** ((TARGET_LUFS - loudness(y, loop=True)) / 20.0)
    gain = min(gain, MAX_DRIVE * PEAK / top)
    y = PEAK * np.tanh(x * gain / PEAK)
    over = true_peak_db(y, loop=True) - TRUE_PEAK_DB
    return y * 10.0 ** (-over / 20.0) if over > 0.0 else y


# ---- sentez -------------------------------------------------------------------
def _band_noise(rng, n, lo, hi):
    """Dairesel (dikişsiz) bant sınırlı gürültü, birim standart sapma."""
    spec = rng.normal(size=n // 2 + 1) + 1j * rng.normal(size=n // 2 + 1)
    f = np.fft.rfftfreq(n, 1.0 / SR)
    spec = spec * smoothstep(lo * 0.7, lo, f) * (1.0 - smoothstep(hi, hi * 1.4, f))
    spec[0] = 0.0
    x = np.fft.irfft(spec, n)
    return x / (x.std() + 1e-12)


def _reverb_ir(rng, n, rt60):
    length = min(n, int(SR * rt60 * 1.1))
    t = np.arange(length) / SR
    ir = rng.normal(size=length) * np.exp(-6.91 * t / rt60)
    pre = int(0.012 * SR)
    ir = np.concatenate([np.zeros(pre), ir[:length - pre]])
    ir = gaussian_filter1d(ir, 2.0)                       # üst frekansları yumuşat
    return ir / (np.sqrt((ir ** 2).sum()) + 1e-12)


def _add_event(buf, start, wave_, pan):
    """Olayı (çan notası, kabarma) dairesel ekler: dosya sonunu ya da başını aşan kısım sarılır, dikiş korunur."""
    n = buf.shape[1]
    idx = (start + np.arange(len(wave_))) % n
    buf[0, idx] += wave_ * (1.0 - pan)
    buf[1, idx] += wave_ * pan


def _slow_env(power, seconds):
    """Güç eğrisinin (n,) yavaş RMS zarfı: kontrol hızında ortalama, dairesel yumuşatma."""
    n = len(power)
    m = max(8, int(n / SR * CONTROL_HZ))
    edges = np.linspace(0, n, m + 1).astype(np.int64)
    acc = np.concatenate([[0.0], np.cumsum(power)])
    mean = (acc[edges[1:]] - acc[edges[:-1]]) / np.maximum(edges[1:] - edges[:-1], 1)
    env = np.sqrt(np.maximum(_smooth(mean, seconds), 0.0))
    return np.interp(np.arange(n) / n, (np.arange(m) + 0.5) / m, env, period=1.0)


def _steady(x):
    """Bir ses grubunun (2, n) kendi yavaş seviye dalgalanmasını (detune vuruşları, açılıp kapanan kısmi sesler)
    büyük oranda düzler: bunlar tınıyı değiştirsin, seviyeyi ve sağ-sol dengeyi görüntü yönetsin. Önce her kanal
    ayrı (denge oynamasın), sonra ortak: stereo (L² + R²) ve mono ((L + R)² / 2) dinleyişin ortalaması, yani
    telefon hoparlöründe de kulaklıkta da seviye aynı kalır. Grubun ortalama seviyesini (RMS) de döndürür."""
    def hold(sig, power):
        env = _slow_env(power, LEVEL_WINDOW)
        ref = float(np.sqrt((env ** 2).mean()))
        if ref < 1e-12:
            return sig, 0.0
        return sig * (ref / np.maximum(env, 0.1 * ref)) ** LEVEL_HOLD, ref

    x = np.stack([hold(x[ch], x[ch] ** 2)[0] for ch in (0, 1)])
    return hold(x, 0.5 * (x ** 2).sum(axis=0) + 0.25 * x.sum(axis=0) ** 2)


def synthesize(plan, style_name, n, start_fraction=0.0, visual=None):
    """Planın görüntüsüne bağlı stereo ambient ses. `start_fraction`: videonun döngüde başladığı nokta.
    `visual`: bitmiş karelerden ölçülen eğriler (`visual_curves`); verilirse ses ekranda görüneni izler.

    Kaynaklar (kısmi sesler, alt bas, gürültü, cam tınısı) durağan üretilir ve yankıdan geçer; görüntüye bağlı
    seviyeler yankıdan SONRA uygulanır. Böylece yankı kuyruğu görüntüyle senkronu bulandırmaz: ekranda hareket
    bittiği an ses de çekilir. Yankının kuyruğu yalnız olaylarda (nota, kabarma) duyulur."""
    st = L.SOUNDS[style_name]
    T = n / SR
    rng = np.random.default_rng([plan.seed, 0x50DA])
    ctl = controls(plan, T, visual)
    u = np.arange(n) / n
    t = np.arange(n) / SR
    w0 = TAU * u
    cx, motion, glint, mass, pan, burst, lines, smoke = (
        _periodic(ctl[k], n) for k in ('cx', 'motion', 'glint', 'mass', 'pan', 'burst', 'lines', 'smoke'))
    rig = plan.rig

    def snap(f):
        return max(1.0, round(f * T)) / T                 # tam sayı çevrim: döngü dikişsiz

    # --- yankı: dairesel evrişim (dikişsiz)
    # İki kanalın yankısı büyük oranda ORTAKTIR (tam bağımsız olsaydı kısmi sesler kanallar arasında rastgele faz
    # farkı alır, bazıları ters fazda kalırdı: telefon hoparlörü gibi mono çalan yerlerde ses birbirini yok eder).
    wet = st['wet']
    common = _reverb_ir(rng, n, st['rt60'])
    room_f = []
    for ch in (0, 1):
        ir = np.sqrt(0.8) * common + np.sqrt(0.2) * _reverb_ir(rng, n, st['rt60'])
        room_f.append(np.fft.rfft(ir / (np.sqrt((ir ** 2).sum()) + 1e-12), n))

    def room(x):
        """Kuru ses + yankısı. `x`: (2, n) ya da iki kanala aynı giden (n,)."""
        x = np.broadcast_to(x, (2, n))
        return np.stack([(1.0 - wet) * x[ch] + wet * 1.4 * np.fft.irfft(np.fft.rfft(x[ch]) * room_f[ch], n)
                         for ch in (0, 1)])

    def pair(f, beat, flip):
        """Hafif detune iki ses: vuruş tam sayı çevrimlidir. Ana ses ortada, ikincisi bir yanda durur: vuruş stereo
        genişlik ve tını kıpırtısı olarak duyulur, mono çalmada (telefon hoparlörü) seviyeyi derin dalgalandırmaz.
        Seviyeyi görüntü yönetir, sesin kendi vuruşu değil."""
        v1 = np.sin(TAU * (f - beat / T) * t + rng.uniform(0, TAU))
        v2 = np.sin(TAU * (f + beat / T) * t + rng.uniform(0, TAU))
        a, b = 0.85 * v1 + 0.45 * v2, 0.85 * v1 - 0.05 * v2
        return np.stack([b, a] if flip else [a, b])

    root = float(rng.choice(st['roots']))
    K = len(st['ratios'])
    # İki grup: gövde (GLINT_HZ altı) ekrandaki hareketle, parlak sesler (üstü) parlamayla yaşar.
    bed = np.zeros((2, n))
    bright = np.zeros((2, n))

    energy = (1.0 - UPPER_FOLLOW) + UPPER_FOLLOW * motion                      # üst sesler hareketle kabarır
    body = ((1.0 - BODY_FOLLOW) + BODY_FOLLOW * mass) * (1.0 - THREAD_THIN * lines)   # gövde dolulukla
    opening = np.clip(cx + OPEN_BY_MOTION * (motion - 0.5), 0.0, 1.0)

    # --- kısmi sesler
    for k, (ratio, amp, beat) in enumerate(zip(st['ratios'], st['amps'], st['beats'])):
        f = snap(root * ratio)
        if f > 0.42 * SR:
            continue
        gate = 0.30 + 0.70 * smoothstep(k / K - 0.10, (k + 1) / K + 0.15, opening * 1.1)   # karmaşıklık ve hareketle açılır
        # tremolo: formun harmonik loblarının aktığı çevrim ve fazla (geometrideki `ph` ile aynı sayılar)
        j = k % len(rig['cycles'])
        trem = 1.0 + st['trem'] * 0.5 * np.cos(float(rig['cycles'][j]) * w0 + float(rig['phases'][j]))
        hi = k / max(K - 1, 1)                            # 0 en alt kısmi .. 1 en üst
        src = room(pair(f, beat, k % 2) * trem)
        if f >= GLINT_HZ:
            bright += src * (amp * gate)
        else:
            bed += src * (amp * gate * ((1.0 - hi) * body + hi * energy))

    # --- alt bas: dolulukla büyür, hızlı değişimlerde hafifçe kısılır
    sub = np.sin(TAU * snap(root / 2.0) * t + rng.uniform(0, TAU))
    bed += room(sub) * (st['sub'] * 0.6 * (0.75 + 0.25 * (1.0 - cx)) * (1.0 - 0.35 * burst) * body)

    # --- nefes/doku gürültüsü: bandı alttaysa (nefes) hareketle kabarır ve duman malzemede artar, tizdeyse (hava)
    # parlak seslerle birlikte parlamayı izler
    lo, hi = st['noise']
    noise = room(np.stack([_band_noise(rng, n, lo, hi) for _ in (0, 1)])) * (st['noise_amp'] * 0.22)
    if lo >= GLINT_HZ:
        bright += noise
    else:
        bed += noise * ((NOISE_FLOOR + (1.0 - NOISE_FLOOR) * motion + 0.50 * burst) * (1.0 + SMOKE_BREATH * smoke))

    # --- seviye görüntüyü izler. Grupların kendi dalgalanması düzlenir, sonra gövde harekete, parlak sesler
    # parlamaya bağlanır. Stilin nabzı (varsa) en güçlü değişim anında doruğa gelecek fazla çalar.
    pulse = np.ones(n)
    if st.get('pulse'):
        cycles = max(1, round(st['pulse'] * T))
        lead = max(ctl['peaks'], key=lambda e: e[1])[0] if ctl['peaks'] else float(rng.uniform(0, 1))
        pulse = 0.55 + 0.45 * (0.5 + 0.5 * np.cos(TAU * cycles * (u - lead)))
    bed, ref = _steady(bed)
    bed *= ((1.0 - FOLLOW_MOTION) + FOLLOW_MOTION * motion) * pulse
    bright, _ = _steady(bright)
    bright *= ((1.0 - GLINT_FOLLOW) + GLINT_FOLLOW * glint) * ((1.0 - BRIGHT_MOTION) + BRIGHT_MOTION * motion)

    # --- cam tınısı: parlama alanıyla birlikte açılıp kapanır (stilin kendi oranlarıyla, üst oktavda). Her nota
    # birkaç Hz aralıklı üç sesin kümesidir: kendi içinde hızlı pırıldar, seviyesi yalnız parlamayı izler.
    level = st.get('glint', 1.0) * GLINT_LEVEL * ref
    if level > 0.0:
        g0 = root
        while g0 < GLINT_HZ:
            g0 *= 2.0
        tones = np.zeros((2, n))
        for ratio, amp in zip(st['ratios'][:3], (1.0, 0.5, 0.25)):
            f = g0 * ratio / st['ratios'][0]
            if f > min(0.42 * SR, 0.85 * st['lowpass']):
                continue
            for df, left, part in ((0.0, 0.5, 1.0), (rng.uniform(2.5, 4.5), 0.85, 0.30),
                                   (-rng.uniform(5.0, 7.5), 0.15, 0.22)):
                v = amp * part * np.sin(TAU * snap(f + df) * t + rng.uniform(0, TAU))
                tones[0] += left * v
                tones[1] += (1.0 - left) * v
        bright += room(tones) * (level * glint ** 1.5)

    # --- stereo denge: ışık ekranda hangi yandaysa ses o yana yaslanır (yalnız seviye: mono çalmada kayıp olmaz).
    # Parlak sesler ışığı daha belirgin izler.
    for group, depth in ((bed, PAN_DEPTH), (bright, 2.0 * PAN_DEPTH)):
        group[0] *= 1.0 - depth * pan
        group[1] *= 1.0 + depth * pan
    bed += bright

    # --- olaylar: her geçişin en hızlı anında cam/çan notası ve alt kabarma. (Aşama sınırında form durgundur:
    # orada çalan nota ekranda hiçbir şeye denk gelmez. Nota, değişimin doruğuna oturur.)
    events = np.zeros((2, n))
    if st['shimmer'] > 0.0:
        scale = [root * 2 * ratio * oct_ for ratio in PENTATONIC for oct_ in (1, 2)]
        for tau, weight in ctl['peaks']:
            f0 = float(scale[int(rng.integers(0, len(scale)))])
            dur = int(SR * st['decay'] * 3.2)
            tt = np.arange(dur) / SR
            tone = np.zeros(dur)
            for pr, pa in ((1.0, 1.0), (2.0 if st['ratios'][1] <= 2.1 else st['ratios'][1], 0.45), (3.01, 0.2)):
                if f0 * pr < 0.42 * SR:
                    tone += pa * np.sin(TAU * f0 * pr * tt + rng.uniform(0, TAU))
            tone *= np.minimum(tt / BELL_ATTACK, 1.0) * np.exp(-tt / st['decay'])      # kısa atak, üstel sönüm
            _add_event(events, int(round(tau * n)), tone * st['shimmer'] * weight * BELL_LEVEL * ref,
                       float(rng.uniform(0.25, 0.75)))
    low = root
    while low > 120.0:
        low /= 2.0
    rise, fall = int(SR * SWELL_RISE), int(SR * SWELL_FALL)
    if rise + fall < n:
        tt = (np.arange(rise + fall) - rise) / SR         # 0 = tepe: tam en hızlı değişim karesi
        shape = np.concatenate([np.sin(0.5 * np.pi * np.arange(rise) / rise) ** 2,
                                np.cos(0.5 * np.pi * np.arange(fall) / fall) ** 2])
        tone = (np.cos(TAU * low * tt) + 0.5 * np.cos(TAU * 2 * low * tt) + 0.25 * np.cos(TAU * 3 * low * tt)) * shape
        for tau, strength in ctl['peaks']:
            _add_event(events, int(round(tau * n)) - rise,
                       tone * 2.0 * SWELL_LEVEL * ref * (0.4 + 0.6 * st['sub']) * strength, 0.5)
    out = bed + room(events)

    # --- ana işlem: alçak/yüksek kesim (FFT), ses yüksekliği, yumuşak sınırlama
    f = np.fft.rfftfreq(n, 1.0 / SR)
    mask = smoothstep(18.0, 40.0, f) * (1.0 - smoothstep(st['lowpass'], st['lowpass'] * 1.3, f))
    out = np.stack([np.fft.irfft(np.fft.rfft(out[ch]) * mask, n) for ch in (0, 1)])
    out = _master(np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0).T).astype(np.float32)

    shift = int(round(start_fraction * n)) % n            # video döngüde bu noktadan başlıyor
    return np.roll(out, -shift, axis=0) if shift else out


def write_wav(path, audio, seed=0):
    """16 bit stereo WAV (±1 LSB üçgen dither)."""
    rng = np.random.default_rng([seed, 0xD17])
    dither = (rng.random(audio.shape) - rng.random(audio.shape)) / 32768.0
    pcm = (np.clip(audio.astype(np.float64) + dither, -1.0, 1.0) * 32767.0).astype('<i2')
    with wave.open(path, 'wb') as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
