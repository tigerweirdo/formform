"""EVRİM MOTORU: her yayın için yeni bir video tarifi (DNA) üretir.

Eski n8n 'GENERATE TEXT' düğümünün birebir karşılığıdır (testlerle eski kodla karşılaştırılır).

  %30 exploration : her şey rastgele (KALICI GEREKSİNİM, EXPLORATION_RATE)
  %70 exploitation:
    - %50 elit melezleme: geçmişin en iyi videolarından biri ebeveyn seçilir, genleri kopyalanır ve
      her gen küçük olasılıkla mutasyona uğrar. Tek tek boyutların göremediği kombinasyonları yakalar.
    - %50 ağırlıklı seçim: dna_weights ağırlıklarıyla.

Öğrenilen boyutlar: structure, palette, motion, lighting, format (süre), material, transformation,
hook (caption açılışı), sound (ses stili), tag (her hashtag ayrı).

`rng` 0 ≤ x < 1 döndüren bir çağrılabilirdir (test için sabit dizi verilebilir). Eski n8n kodundan gelen
alanlar (yapı, palet, hareket, ışık, format, dönüşümler, malzemeler, hook) aynı rastgele sayı SIRASIYLA seçilir.
Sonradan eklenenler (ses, hashtag, açıklama satırı) dizinin sonunda çekilir, böylece eski alanlar değişmez.
"""
import math
import re
from datetime import timezone
from zoneinfo import ZoneInfo

from . import caption as captions, config
from .jsnum import finite, fold_sum, num
from formform import library as L

EXPLORATION_RATE = 0.30      # kalıcı gereksinim
ELITE_RATE = 0.50            # exploitation içinde elit melezleme payı
ELITE_POOL = 5               # en iyi kaç video ebeveyn olabilir
MIN_SCORED_FOR_ELITES = 4    # elit için en az skorlu video
GENE_MUTATION = 0.25         # tek değerli gen mutasyon olasılığı
LIST_MUTATION = 0.35         # liste elemanı mutasyon olasılığı

STRUCTURES = list(L.STRUCTURES)
PALETTES = list(L.PALETTES)
MATERIALS = list(L.MATERIALS)
TRANSFORMATIONS = list(L.TRANSFORMATIONS)
MOTION_QUALITIES = list(L.MOTIONS)
LIGHTING_STYLES = list(L.LIGHTING)
SOUND_NAMES = list(L.SOUNDS)

# Çıktı çözünürlüğü: ince teller 1080p'de keskin kalır (Instagram yeniden sıkıştırır, 720p'de yumuşardı).
RENDER_WIDTH, RENDER_HEIGHT = 1080, 1920
TAG_COUNT = 4                # #formform'a ek hashtag sayısı (Instagram: az sayıda, alakalı)

# Süre/format. Dönüşüm sayısı aşama sayısını, malzeme sayısı dönüşüm + 1'i belirler.
# Format adı videos.csv'deki loop_strategy sütununa yazılır ve öğrenilir.
FORMATS = [
    {'name': 'seamless_8s', 'duration': 8, 'fps': 30, 'transformations': 4},
    {'name': 'seamless_15s', 'duration': 15, 'fps': 30, 'transformations': 7},
    {'name': 'seamless_30s', 'duration': 30, 'fps': 24, 'transformations': 10},
]
FORMAT_NAMES = [f['name'] for f in FORMATS]

CAPTION_OPENINGS = [
    'A form without a final state.',
    'Nothing remains in one form for long.',
    'Form exists only while changing.',
    'One state becoming another.',
    'A temporary geometry.',
    'Movement without destination.',
    'A structure passing through itself.',
    'Between one form and the next.',
    'No stable state.',
    'Transformation as structure.',
    'Every form is temporary.',
    'A shape becoming its next condition.',
    'Light remembering a shape.',
    'The shape is the motion.',
    'Nothing here stays still.',
    'A form, unfinished.',
    'Quiet geometry in motion.',
    'Where one shape ends, another begins.',
    'Held for a moment, then released.',
    'The space between two forms.',
    'It never settles.',
    'A slow unfolding.',
    'Structure as a kind of weather.',
    'Everything is passing through.',
    'A shape that keeps leaving itself.',
    'Drawn in light, undone in light.',
    'This form will not last.',
    'Almost a shape.',
    'A loop with no beginning.',
    'Continuity without a center.',
    'Repetition, never the same twice.',
    'Slowly becoming something else.',
    'An object made of change.',
    'The shape of a pause.',
    'Light finding a structure.',
    'Always arriving, never arrived.',
    'Gravity, imagined.',
    'A study in staying unfinished.',
    'Soft geometry, endlessly.',
    'Turning without turning back.',
    'Form as a verb.',
    'Depth made of light.',
    'Moving toward nothing in particular.',
    'A thing that cannot be held.',
    'Patterns thinking out loud.',
    'The shape listens.',
    'Slow light, folded.',
    'Open, close, open.',
]

# İkinci satır: marka imzası (öğrenilmez, çeşitlilik içindir)
CAPTION_SECOND_LINES = [
    'form/form — morphological study.',
    'form/form — continuous transformation.',
    'form/form — study in unstable geometry.',
    'form/form — an evolving structure.',
    'form/form — recursive motion study.',
    'form/form — transformation study.',
    'form/form — continuous form study.',
    'form/form — motion without a final state.',
    'form/form — study in light and motion.',
    'form/form — a quiet transformation.',
    'form/form — notes on a changing shape.',
    'form/form — slow geometry.',
    'form/form — study in continuity.',
    'form/form — drawn in light.',
    'form/form — a form in transit.',
    'form/form — procedural light study.',
    'form/form — an unfinished shape.',
    'form/form — loop study.',
    'form/form — geometry, softly.',
    'form/form — a study in change.',
    'form/form — studies in form.',
    'form/form — light, folded.',
    'form/form — in continuous motion.',
    'form/form — drift.',
]

# Açıklama satırı: videonun gerçek özelliklerinden (yapı, renk, süre, ses) üretilir. Hem doğru hem arama için anahtar kelime.
COLOR_WORDS = {
    'crimson-magenta': 'crimson and magenta light',
    'graphite-silver': 'graphite and silver light',
    'violet-pink': 'violet and pink light',
    'amber-black': 'amber light on black',
    'blue-graphite': 'blue and graphite light',
    'scarlet-burgundy': 'scarlet and burgundy light',
}

DESCRIPTOR_TEMPLATES = [
    '{S} in {colors}. A {dur}-second seamless loop.',
    '{S}, {colors}. Seamless {dur}-second loop. Sound on.',
    'Procedural light sculpture: {s}, {colors}.',
    '{Colors}. {S}. A {dur}-second loop with ambient sound.',
    'A study in {colors}. {S}, looping without end.',
    '{S} / {colors} / {dur}s loop. Best with sound.',
    'Generative abstract motion. {S} in {colors}.',
    '{S}, rendered as light. {dur}-second seamless loop, sound on.',
    'Abstract loop: {s} in {colors}. Headphones recommended.',
    '{S} in {colors}, with ambient sound. Seamless loop.',
    '{S} in {colors}. Sound: {sound}.',
    '{S}, {colors}. {dur}-second loop. Sound: {sound}.',
]

# Hashtag havuzu: ortak havuz + paletin renk etiketleri. Her hashtag ayrı bir gendir ve öğrenilir.
TAGS_CORE = [
    '#abstractart', '#generativeart', '#motiondesign', '#loopart', '#abstractmotion', '#digitalart',
    '#proceduralart', '#creativecoding', '#visualart', '#lightart', '#minimalart', '#newmediaart',
    '#seamlessloop', '#hypnotic', '#abstractvisuals', '#generativedesign', '#experimentalart',
    '#motiongraphics', '#darkaesthetic', '#morphology', '#generativevisuals', '#motionart',
    '#visualexperiment', '#soundandvision',
]

PALETTE_TAGS = {
    'crimson-magenta': ['#crimson', '#magenta'],
    'graphite-silver': ['#monochromeart', '#silver'],
    'violet-pink': ['#violetaesthetic', '#pinkaesthetic'],
    'amber-black': ['#amberlight', '#darkart'],
    'blue-graphite': ['#blueaesthetic', '#monochromeart'],
    'scarlet-burgundy': ['#redaesthetic', '#burgundy'],
}


def is_scored(row):
    vid = str(row.get('video_id') or '')
    if not vid or vid.startswith('TEST_'):
        return False
    score = row.get('performance_score')
    if score is None or str(score).strip() == '':
        return False
    return finite(num(score))


def _iso_utc(now):
    utc = now.astimezone(timezone.utc)
    return utc.strftime('%Y-%m-%dT%H:%M:%S.') + f'{utc.microsecond // 1000:03d}Z'


def generate(videos, dna_rows, rng, now, formats=None, size=None):
    """Yeni bir tarif üretir. now: saat dilimli datetime.

    formats: izin verilen format adları (bütçe bekçisi kısıtlar, None = hepsi). size: (genişlik, yükseklik)."""
    local = now.astimezone(ZoneInfo(config.TIMEZONE))
    timestamp = local.strftime('%Y%m%d_%H%M%S')

    def pick(arr):
        return arr[int(math.floor(rng() * len(arr)))]

    selection_mode = 'exploration' if rng() < EXPLORATION_RATE else 'exploitation'

    # -- dna_weights ----------------------------------------------------------
    lookup = {}
    for row in dna_rows:
        if not row.get('dimension') or not row.get('value'):
            continue
        w = num(row.get('weight'))
        if not finite(w) or w <= 0:
            continue
        lookup[f"{str(row['dimension']).strip()}::{str(row['value']).strip()}"] = w

    def get_weight(dimension, value):
        return lookup.get(f'{dimension}::{value}', 1)

    def weighted_pick(arr, dimension, exclude=()):
        pool = [v for v in arr if v not in exclude]
        if not pool:
            return None
        if selection_mode == 'exploration':
            return pick(pool)
        candidates = [(v, max(0.0001, float(get_weight(dimension, v)) or 1.0)) for v in pool]
        total = fold_sum(w for _, w in candidates)
        r = rng() * total
        for value, w in candidates:
            r -= w
            if r <= 0:
                return value
        return candidates[-1][0]

    # -- elit ebeveyn ---------------------------------------------------------
    history = [dict(r) for r in videos if is_scored(r)]
    use_elite = (selection_mode == 'exploitation' and len(history) >= MIN_SCORED_FOR_ELITES
                 and rng() < ELITE_RATE)
    parent = None
    if use_elite:
        top = sorted(history, key=lambda r: -num(r['performance_score']))[:ELITE_POOL]
        total = fold_sum(max(1.0, num(r['performance_score'])) for r in top)
        r = rng() * total
        parent = top[-1]
        for row in top:
            r -= max(1.0, num(row['performance_score']))
            if r <= 0:
                parent = row
                break

    def parent_list(value):
        if parent is None:
            return []
        return [v.strip() for v in str(value or '').split(', ') if v.strip()]

    parent_hook = str(parent.get('caption') or '').split('\n')[0].strip() if parent else None

    # -- gen seçimi -----------------------------------------------------------
    def gene(arr, dimension, parent_value):
        if parent is not None and parent_value in arr and rng() >= GENE_MUTATION:
            return parent_value
        return weighted_pick(arr, dimension)

    def list_gene(arr, dimension, count, parent_values):
        source = [v for v in parent_values if v in arr]
        out = []
        for i in range(count):
            value = None
            if source:
                value = source[(i * len(source)) // count]
            if value is None or value in out or rng() < LIST_MUTATION:
                value = weighted_pick(arr, dimension, out)
            out.append(pick(arr) if value is None else value)
        return out

    pv = (lambda key: parent.get(key) if parent else None)
    structure = gene(STRUCTURES, 'structure', pv('structure'))
    palette = gene(PALETTES, 'palette', pv('palette'))
    motion_quality = gene(MOTION_QUALITIES, 'motion', pv('motion_quality'))
    lighting = gene(LIGHTING_STYLES, 'lighting', pv('lighting'))
    format_name = gene([f for f in FORMAT_NAMES if formats is None or f in formats] or FORMAT_NAMES, 'format',
                       pv('loop_strategy'))
    fmt = next(f for f in FORMATS if f['name'] == format_name)

    transformations = list_gene(TRANSFORMATIONS, 'transformation', fmt['transformations'],
                                parent_list(pv('transformations')))
    materials = list_gene(MATERIALS, 'material', fmt['transformations'] + 1, parent_list(pv('materials')))
    hook = gene(CAPTION_OPENINGS, 'hook', parent_hook)

    signature = pick(CAPTION_SECOND_LINES)           # eski kodla aynı sırada çekilir

    # Sonradan eklenenler (eski rastgele sayı dizisini bozmasın diye en sonda): ses, hashtag'ler, açıklama satırı
    sound = gene(SOUND_NAMES, 'sound', pv('sound'))
    tag_pool = TAGS_CORE + [t for t in PALETTE_TAGS[palette] if t not in TAGS_CORE]
    tags = list_gene(tag_pool, 'tag', TAG_COUNT, captions.tags(parent.get('caption')) if parent else [])
    template = pick(DESCRIPTOR_TEMPLATES)
    colors = COLOR_WORDS[palette]
    descriptor = template.format(S=structure.capitalize(), s=structure, colors=colors,
                                 Colors=colors[0].upper() + colors[1:], dur=fmt['duration'], sound=sound)

    # İlk iki satır Instagram'da "daha fazla"dan önce görünür: açılış cümlesi ve ne olduğu
    caption = f"{hook}\n\n{descriptor}\n\n{signature}\n\n#formform {' '.join(tags)}"

    safe = re.sub(r'^_|_$', '', re.sub(r'[^a-z0-9]+', '_', structure.lower()))
    # video_id hem kayıt anahtarı hem render tohumudur (seed = crc32(video_id)).
    video_id = f'FF_{timestamp}'
    file_name = f'formform_{safe}_{palette}_{timestamp}.mp4'

    render_spec = {
        'video_id': video_id, 'structure': structure, 'palette': palette, 'motion_quality': motion_quality,
        'lighting': lighting, 'materials': materials, 'transformations': transformations,
        'duration': fmt['duration'], 'fps': fmt['fps'], 'loop': 'seamless',
        'width': (size or (RENDER_WIDTH, RENDER_HEIGHT))[0], 'height': (size or (RENDER_WIDTH, RENDER_HEIGHT))[1],
        'sound': sound,
    }
    metadata = {
        'brand': 'FORM/FORM', 'structure': structure, 'palette': palette, 'materials': materials,
        'transformations': transformations, 'motion_quality': motion_quality, 'lighting': lighting,
        'selection_mode': selection_mode,
        'evolution': 'elite' if use_elite else ('random' if selection_mode == 'exploration' else 'weights'),
        'parent_id': parent.get('video_id', '') if parent else '',
        'structure_weight': get_weight('structure', structure),
        'palette_weight': get_weight('palette', palette),
        'motion_weight': get_weight('motion', motion_quality),
        'lighting_weight': get_weight('lighting', lighting),
        'format': fmt['name'], 'hook': hook, 'sound': sound, 'tags': tags,
        'loop_strategy': fmt['name'],      # videos.csv'deki loop_strategy: format adı
        'renderer': 'procedural_v3',
        'created_at': _iso_utc(now),
    }
    return {
        'brand': 'FORM/FORM', 'video_id': video_id, 'render_spec': render_spec,
        # Render sonunda release gövdesi olur, yayın akışı videoyla birlikte buradan okur.
        'release_payload': dict(render_spec, caption=caption, file_name=file_name, metadata=metadata),
        'caption': caption, 'file_name': file_name, 'metadata': metadata,
    }
