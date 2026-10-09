"""FORM/FORM tasarım kütüphanesi.

Bu dosya adların tek kaynağıdır: `ops/evolve.py` (evrim motoru) listeleri buradan okur.
Yeni bir yapı, palet, malzeme, dönüşüm, hareket ya da ışık buraya eklenince otomatik olarak
üretilebilir olur. Malzeme ve dönüşüm adları `, ` içeremez (videos.csv'de liste ayırıcısıdır).

Bir form, `KNOBS` içindeki sürekli sayılarla tanımlanır. Her yapı bir başlangıç
vektörü verir, her dönüşüm bu vektörü değiştirir. Aşamalar arası geçiş bu sayıların
interpolasyonudur, yani morph gerçek bir geometri değişimidir. Kesme ya da çapraz geçiş yoktur.
"""

# ---------------------------------------------------------------------------
# Geometri düğmeleri: (varsayılan, alt sınır, üst sınır)
# ---------------------------------------------------------------------------
KNOBS = {
    'R': (1.0, 0.30, 1.7),          # taban yarıçap
    'width': (0.28, 0.025, 0.95),    # şerit yarı genişliği (R oranı)
    'sheets': (3.0, 1.0, 11.0),     # görünür yaprak sayısı (kesirli olabilir)
    'spread': (0.10, 0.0, 1.3),     # yapraklar arası z ayrımı
    'phase': (0.5, 0.0, 1.0),       # yapraklar arası açı kayması
    'twist': (0.0, -1.3, 1.3),      # tur başına şerit burulması
    'turns': (1.0, 1.0, 3.2),       # dönüş sayısı (1 = kapalı halka)
    'pitch': (0.0, 0.0, 0.9),       # spiralde yarıçap daralması
    'lift': (0.15, 0.0, 1.0),       # z dalgalanması
    'funnel': (0.0, -1.0, 1.0),     # u boyunca z kayması (huni)
    'h1': (0.0, -0.6, 0.6),         # merkez kayması (limacon)
    'h2': (0.0, 0.0, 0.40),          # radyal harmonikler
    'h3': (0.0, 0.0, 0.34),
    'h4': (0.0, 0.0, 0.34),
    'h5': (0.0, 0.0, 0.28),
    'aspect': (1.0, 0.45, 1.3),     # y ölçeği
    'stretch': (0.0, -0.3, 0.8),    # dikey uzama
    'fold': (0.0, 0.0, 0.95),        # şerit boyunca katlanma
    'fold_f': (2.0, 1.0, 4.0),
    'warp': (0.0, 0.0, 0.32),        # sıvılaşma
    'facet': (0.0, 0.0, 1.0),       # 0 pürüzsüz, 1 kristal
    'frag': (0.0, 0.0, 0.7),        # parçalanma
    'taper': (0.0, 0.0, 0.8),       # yapraklar arası daralma (girdap)
    'ext': (0.0, 0.0, 0.95),         # tek yapraklarda uzanan uzantılar
    'sheet_tilt': (0.0, 0.0, 0.95),  # yaprakların birbirine eğimi (jiroskop)
    'slide': (0.0, 0.0, 0.7),       # yaprakların zıt yönde kayması
    'core': (0.0, 0.0, 1.0),        # merkez çekirdek
    'tilt_x': (0.5, -1.4, 1.4),     # tüm formun eğimi
    'tilt_y': (0.15, -1.0, 1.0),
}

# ---------------------------------------------------------------------------
# Yapılar: başlangıç vektörü (belirtilmeyenler varsayılan)
# ---------------------------------------------------------------------------
STRUCTURES = {
    'central ring': dict(
        R=1.0, width=0.30, sheets=2.0, spread=0.14, phase=0.5, twist=0.5, lift=0.10,
        h2=0.05, fold=0.18, tilt_x=0.55, tilt_y=0.18),
    'folded mass': dict(
        R=0.62, width=0.62, sheets=5.0, spread=0.35, phase=0.7, twist=0.25, lift=0.32,
        h3=0.16, h2=0.10, fold=0.95, fold_f=3.0, tilt_x=0.45, tilt_y=0.3),
    'radial bloom': dict(
        R=0.95, width=0.30, sheets=7.0, spread=0.10, phase=1.0, twist=0.0, lift=0.30,
        h4=0.30, h2=0.14, fold=0.55, fold_f=2.0, sheet_tilt=0.12, tilt_x=0.75, tilt_y=0.0),
    'spiral form': dict(
        R=1.0, width=0.20, sheets=3.0, spread=0.10, phase=0.33, twist=0.30, turns=3.0,
        pitch=0.70, lift=0.12, funnel=0.65, fold=0.15, tilt_x=0.35, tilt_y=0.25),
    'floating core': dict(
        R=0.55, width=0.34, sheets=5.0, spread=0.22, phase=0.8, twist=0.2, lift=0.22,
        ext=0.85, core=0.9, sheet_tilt=0.55, h2=0.10, fold=0.25, tilt_x=0.5, tilt_y=0.2),
    'asymmetric loop': dict(
        R=1.0, width=0.28, sheets=3.0, spread=0.16, phase=0.45, twist=0.45, lift=0.20,
        aspect=0.62, h1=0.30, h2=0.30, h3=0.18, fold=0.30, tilt_x=0.4, tilt_y=0.3),
    'layered vortex': dict(
        R=1.05, width=0.17, sheets=8.0, spread=0.85, phase=0.18, twist=0.55, lift=0.10,
        taper=0.62, funnel=0.35, h2=0.04, fold=0.20, tilt_x=0.95, tilt_y=0.08),
    'folded ribbon core': dict(
        R=0.72, width=0.22, sheets=5.0, spread=0.10, phase=0.4, twist=0.85, lift=0.12,
        sheet_tilt=1.0, h2=0.06, fold=0.25, tilt_x=0.35, tilt_y=0.3),
}

# ---------------------------------------------------------------------------
# Dönüşümler: (düğme, işlem, değer). işlemler: mul, add
# ---------------------------------------------------------------------------
TRANSFORMATIONS = {
    'stretch outward into long curved layers':
        [('R', 'mul', 1.22), ('width', 'mul', 1.35), ('pitch', 'add', 0.18), ('turns', 'add', 0.35),
         ('lift', 'add', 0.12), ('aspect', 'mul', 1.08)],
    'fold inward toward the central opening':
        [('fold', 'add', 0.55), ('R', 'mul', 0.80), ('width', 'mul', 1.30), ('facet', 'add', 0.10),
         ('sheet_tilt', 'add', 0.15)],
    'split into several thin connected flowing ribbons':
        [('sheets', 'add', 3.5), ('width', 'mul', 0.45), ('spread', 'add', 0.30), ('phase', 'add', 0.25),
         ('twist', 'add', 0.25)],
    'spiral tightly around the central axis':
        [('turns', 'add', 1.6), ('pitch', 'add', 0.45), ('twist', 'add', 0.55), ('funnel', 'add', 0.30),
         ('width', 'mul', 0.80)],
    'expand radially into broad translucent sheets':
        [('R', 'mul', 1.30), ('width', 'mul', 1.70), ('spread', 'add', 0.20), ('h4', 'add', 0.12),
         ('tilt_x', 'add', -0.15)],
    'compress into a dense sculptural core':
        [('R', 'mul', 0.62), ('width', 'mul', 1.45), ('sheets', 'add', 2.5), ('lift', 'add', 0.30),
         ('core', 'add', 0.55), ('spread', 'mul', 0.7)],
    'fragment into controlled floating planes while remaining visually connected':
        [('frag', 'add', 0.75), ('facet', 'add', 0.35), ('spread', 'add', 0.30), ('width', 'mul', 1.15)],
    'liquefy into smooth flowing curves':
        [('warp', 'add', 0.22), ('facet', 'add', -1.0), ('frag', 'add', -1.0), ('fold', 'mul', 0.7),
         ('h2', 'add', 0.10)],
    'unfurl into layered ribbon structures':
        [('twist', 'mul', 0.35), ('width', 'mul', 1.55), ('spread', 'add', 0.45), ('sheets', 'add', 1.5),
         ('fold', 'mul', 0.6)],
    'twist into increasingly complex overlapping folds':
        [('twist', 'add', 0.95), ('fold', 'add', 0.45), ('fold_f', 'add', 1.5), ('h3', 'add', 0.14),
         ('h5', 'add', 0.08)],
    'open gradually into a wider radial configuration':
        [('R', 'mul', 1.28), ('width', 'mul', 0.85), ('h4', 'add', 0.16), ('tilt_x', 'add', -0.25),
         ('sheet_tilt', 'mul', 0.4), ('core', 'mul', 0.3)],
    'collapse into narrow luminous strands':
        [('width', 'mul', 0.20), ('sheets', 'add', 4.0), ('spread', 'add', 0.35), ('twist', 'add', 0.40),
         ('fold', 'mul', 0.3)],
    'bend into a flowing asymmetric configuration':
        [('aspect', 'mul', 0.72), ('h1', 'add', 0.28), ('h2', 'add', 0.22), ('stretch', 'add', 0.12),
         ('tilt_y', 'add', 0.35)],
    'transform from smooth surfaces into angular crystalline structures':
        [('facet', 'add', 1.0), ('warp', 'mul', 0.1), ('fold', 'add', 0.25), ('fold_f', 'add', 1.0)],
    'stretch vertically before curling back toward the center':
        [('stretch', 'add', 0.55), ('aspect', 'mul', 0.85), ('turns', 'add', 0.5), ('pitch', 'add', 0.25),
         ('funnel', 'add', 0.2)],
    'separate into layered sheets that slide through one another':
        [('slide', 'add', 0.65), ('spread', 'add', 0.45), ('sheets', 'add', 1.5), ('phase', 'add', 0.15),
         ('width', 'mul', 1.15)],
}

# ---------------------------------------------------------------------------
# Malzemeler: görüntü stili. Aşamalar arasında bunlar da interpolasyonla geçer.
#   alpha    şeffaflık/yoğunluk       soft   yumuşatma (0..1)
#   sheen    parlaklık                spec   parlaklık keskinliği
#   rim      kenar ışığı              emit   iç ışıma
#   lines    0 yüzey, 1 ince tel      streak ince boyuna çizgilenme
#   smoke    duman modülasyonu        refl   ortam yansıması
#   opaque   ön yüzeyin baskınlığı    facet_shade yüz başına düz gölge
# ---------------------------------------------------------------------------
STYLE_KEYS = ('alpha', 'soft', 'sheen', 'spec', 'rim', 'emit', 'lines', 'streak', 'smoke', 'refl',
              'opaque', 'facet_shade')

MATERIALS = {
    'smooth liquid glass': dict(alpha=0.55, soft=0.30, sheen=0.9, spec=70, rim=1.25, emit=0.10,
                                lines=0.0, streak=0.0, smoke=0.0, refl=0.45, opaque=0.0, facet_shade=0.0),
    'translucent flowing silk-like surfaces': dict(alpha=0.42, soft=0.55, sheen=0.55, spec=18, rim=0.55,
                                                   emit=0.20, lines=0.0, streak=0.55, smoke=0.0, refl=0.10,
                                                   opaque=0.0, facet_shade=0.0),
    'glossy molten surfaces': dict(alpha=0.95, soft=0.12, sheen=1.5, spec=90, rim=0.40, emit=0.05,
                                   lines=0.0, streak=0.0, smoke=0.0, refl=0.85, opaque=0.75, facet_shade=0.0),
    'soft translucent crystalline sheets': dict(alpha=0.40, soft=0.12, sheen=1.1, spec=45, rim=1.0, emit=0.10,
                                                lines=0.0, streak=0.0, smoke=0.0, refl=0.30, opaque=0.0,
                                                facet_shade=0.85),
    'luminous ribbon-like surfaces': dict(alpha=0.80, soft=0.50, sheen=0.7, spec=25, rim=0.35, emit=0.85,
                                          lines=0.0, streak=0.20, smoke=0.0, refl=0.0, opaque=0.0,
                                          facet_shade=0.0),
    'smoky transparent layers': dict(alpha=0.32, soft=1.0, sheen=0.3, spec=10, rim=0.25, emit=0.30,
                                     lines=0.0, streak=0.0, smoke=1.0, refl=0.0, opaque=0.0, facet_shade=0.0),
    'polished reflective fluid': dict(alpha=0.75, soft=0.20, sheen=1.7, spec=130, rim=0.50, emit=0.0,
                                      lines=0.0, streak=0.0, smoke=0.0, refl=1.0, opaque=0.55, facet_shade=0.0),
    'fine glowing threads': dict(alpha=1.15, soft=0.20, sheen=0.8, spec=40, rim=0.60, emit=0.60,
                                 lines=1.0, streak=0.0, smoke=0.0, refl=0.0, opaque=0.0, facet_shade=0.0),
    'thin translucent folded planes': dict(alpha=0.34, soft=0.10, sheen=0.8, spec=30, rim=1.45, emit=0.10,
                                           lines=0.0, streak=0.0, smoke=0.0, refl=0.10, opaque=0.0,
                                           facet_shade=0.30),
    'dense glossy sculptural surfaces': dict(alpha=1.0, soft=0.10, sheen=1.2, spec=75, rim=0.30, emit=0.0,
                                             lines=0.0, streak=0.0, smoke=0.0, refl=0.60, opaque=0.90,
                                             facet_shade=0.0),
    'semi-transparent layered glass': dict(alpha=0.50, soft=0.25, sheen=0.9, spec=55, rim=1.65, emit=0.12,
                                           lines=0.0, streak=0.0, smoke=0.0, refl=0.35, opaque=0.0,
                                           facet_shade=0.0),
    'soft reflective curved sheets': dict(alpha=0.55, soft=0.45, sheen=0.9, spec=30, rim=0.60, emit=0.05,
                                          lines=0.0, streak=0.0, smoke=0.0, refl=0.70, opaque=0.15,
                                          facet_shade=0.0),
}

# ---------------------------------------------------------------------------
# Paletler (doğrusal RGB, 0..1). dark: gölge, mid: ana ton, accent: ikinci ton,
# light: parlama, spec: yansıma rengi.
# ---------------------------------------------------------------------------
PALETTES = {
    'crimson-magenta': dict(dark=(0.10, 0.004, 0.018), mid=(0.62, 0.025, 0.085), accent=(0.78, 0.07, 0.42),
                            light=(1.0, 0.42, 0.62), spec=(1.0, 0.72, 0.84)),
    'graphite-silver': dict(dark=(0.025, 0.027, 0.032), mid=(0.28, 0.295, 0.33), accent=(0.55, 0.58, 0.64),
                            light=(0.86, 0.89, 0.95), spec=(1.0, 1.0, 1.0)),
    'violet-pink': dict(dark=(0.035, 0.012, 0.11), mid=(0.28, 0.07, 0.62), accent=(0.78, 0.20, 0.56),
                        light=(1.0, 0.68, 0.84), spec=(1.0, 0.86, 0.94)),
    'amber-black': dict(dark=(0.055, 0.020, 0.004), mid=(0.62, 0.22, 0.020), accent=(0.86, 0.50, 0.075),
                        light=(1.0, 0.76, 0.34), spec=(1.0, 0.90, 0.62)),
    'blue-graphite': dict(dark=(0.010, 0.014, 0.055), mid=(0.10, 0.16, 0.50), accent=(0.38, 0.45, 0.66),
                          light=(0.70, 0.82, 1.0), spec=(0.92, 0.96, 1.0)),
    'scarlet-burgundy': dict(dark=(0.085, 0.006, 0.012), mid=(0.80, 0.030, 0.040), accent=(0.45, 0.012, 0.045),
                             light=(1.0, 0.50, 0.46), spec=(1.0, 0.82, 0.78)),
}

# ---------------------------------------------------------------------------
# Işık stilleri
#   key: ana ışık yönü (x, y, z)   diffuse/wrap: yüzey ışığı   rim: kenar
#   spec: yansıma çarpanı          inner: iç ışıma   contrast: gama   expo: pozlama
# ---------------------------------------------------------------------------
LIGHTING = {
    'soft directional studio lighting with deep shadows':
        dict(key=(-0.55, 0.65, 0.70), diffuse=1.15, wrap=0.35, rim=0.45, spec=0.9, inner=0.12,
             contrast=1.45, expo=1.00),
    'subtle rim lighting emphasizing translucent edges':
        dict(key=(0.60, 0.30, -0.40), diffuse=0.40, wrap=0.55, rim=1.60, spec=0.6, inner=0.18,
             contrast=1.20, expo=1.00),
    'controlled cinematic lighting with bright internal highlights':
        dict(key=(0.35, 0.75, 0.55), diffuse=0.85, wrap=0.45, rim=0.75, spec=1.3, inner=0.55,
             contrast=1.30, expo=1.08),
    'dark editorial lighting with gentle reflective highlights':
        dict(key=(-0.30, 0.45, 0.85), diffuse=0.55, wrap=0.50, rim=0.55, spec=1.1, inner=0.08,
             contrast=1.65, expo=0.80),
    'minimal high-contrast lighting emphasizing surface depth':
        dict(key=(-0.80, 0.25, 0.55), diffuse=1.45, wrap=0.05, rim=0.25, spec=1.0, inner=0.04,
             contrast=1.95, expo=1.00),
}

# ---------------------------------------------------------------------------
# Hareket nitelikleri: zaman eğrisi ve ikincil hareket
#   dwell    aşamalarda bekleme (0 = sürekli akış, 1 = belirgin duraklama)
#   accel    zamanın üssü (1 = doğrusal, >1 sonda hızlanır)
#   burst    kısa hızlı değişim gücü
#   energy   ikincil hareket hızı     spin   form dönüş hızı (rad/s)
# ---------------------------------------------------------------------------
MOTIONS = {
    'slow, deliberate and hypnotic': dict(dwell=0.55, accel=1.00, burst=0.55, energy=0.55, spin=0.10),
    'fluid with gradually increasing intensity': dict(dwell=0.15, accel=1.35, burst=0.65, energy=0.80, spin=0.16),
    'elegant and continuously evolving': dict(dwell=0.05, accel=1.00, burst=0.50, energy=0.70, spin=0.12),
    'calm at first, then increasingly complex': dict(dwell=0.30, accel=1.65, burst=0.70, energy=0.65, spin=0.14),
    'precise, smooth and dreamlike': dict(dwell=0.40, accel=1.00, burst=0.45, energy=0.50, spin=0.08),
    'controlled but progressively more energetic': dict(dwell=0.20, accel=1.45, burst=0.85, energy=1.00,
                                                         spin=0.24),
}

# ---------------------------------------------------------------------------
# Ses stilleri (formform/audio.py bunları sentezler; telifsiz, tamamen prosedürel ambient)
#   roots     temel notalar (Hz), video başına tohumdan biri seçilir
#   ratios    kısmi frekans oranları, amps her kısmi için genlik
#   beats     her kısmi için vuruş (detune) çevrimi: tam sayı çevrim, döngü dikişsiz kalır
#   sub       alt bas seviyesi        noise (alt, üst Hz) ve noise_amp  nefes/doku gürültüsü
#   shimmer   her geçişin en hızlı anında çalan çan/cam notalarının seviyesi, decay süresi (sn)
#   pulse     (isteğe bağlı) saniyedeki nabız çevrimi, trem tremolo derinliği
#   glint     (isteğe bağlı) parlamayı izleyen tiz cam tınısının çarpanı, varsayılan 1
#   rt60      yankı süresi, wet yankı oranı, lowpass üst kesim (Hz)
# Ad listesi evrim motoruna (ops/evolve.py) buradan gider. Eklenen her stil kendiliğinden seçilebilir olur.
# ---------------------------------------------------------------------------
SOUNDS = {
    'deep drone': dict(roots=(49.0, 55.0, 58.27, 65.41), ratios=(1, 2, 3, 4, 5, 6),
                       amps=(1.0, 0.62, 0.36, 0.24, 0.16, 0.10), beats=(2, 3, 4, 5, 6, 7), sub=0.80,
                       noise=(80, 700), noise_amp=0.22, shimmer=0.12, decay=2.5, trem=0.22,
                       rt60=3.4, wet=0.50, lowpass=6500),
    'glass harmonics': dict(roots=(174.6, 196.0, 220.0, 246.9), ratios=(1, 2.756, 5.404, 8.933, 13.34),
                            amps=(1.0, 0.55, 0.30, 0.16, 0.08), beats=(1, 2, 3, 4, 5), sub=0.25,
                            noise=(3000, 11000), noise_amp=0.08, shimmer=0.90, decay=1.8, trem=0.30,
                            rt60=3.0, wet=0.55, lowpass=14000),
    'airy pad': dict(roots=(110.0, 123.5, 130.8, 146.8), ratios=(1, 1.5, 2, 3, 4, 5),
                     amps=(0.9, 0.6, 0.7, 0.4, 0.25, 0.15), beats=(3, 4, 5, 6, 7, 8), sub=0.35,
                     noise=(400, 5000), noise_amp=0.26, shimmer=0.35, decay=2.2, trem=0.35,
                     rt60=4.0, wet=0.60, lowpass=9000),
    'sub pulse': dict(roots=(41.2, 43.65, 49.0, 55.0), ratios=(1, 2, 3, 5), amps=(1.0, 0.5, 0.25, 0.12),
                      beats=(1, 2, 3, 4), sub=1.0, noise=(60, 400), noise_amp=0.18, shimmer=0.0, decay=1.0,
                      pulse=0.6, trem=0.10, rt60=2.4, wet=0.35, lowpass=4500),
    'metal shimmer': dict(roots=(329.6, 392.0, 440.0, 493.9), ratios=(1, 2.32, 3.97, 5.51, 7.1),
                          amps=(0.8, 0.6, 0.45, 0.30, 0.20), beats=(4, 5, 6, 7, 8), sub=0.15,
                          noise=(5000, 14000), noise_amp=0.07, shimmer=0.60, decay=1.4, trem=0.45,
                          rt60=2.6, wet=0.50, lowpass=15000),
}
