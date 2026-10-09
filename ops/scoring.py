"""Öğrenme hesapları: hangi video ölçülecek, ölçümlerin ayrıştırılması, performans skoru, DNA ağırlıkları.

Eski n8n LEARNING workflow'undaki Code düğümlerinin birebir karşılığıdır (testlerle eski kodla karşılaştırılır).
"""
import math
import re

from . import caption as captions
from .jsnum import blank, finite, fold_sum, js_round, num, num_or0, parse_time_ms, tidy

HOUR_MS = 60 * 60 * 1000

# Öğrenilen boyutlar. field: videos.csv'deki sütun, dimension: dna_weights'teki ad.
SINGLE = [
    ('structure', 'structure'),
    ('palette', 'palette'),
    ('motion_quality', 'motion'),
    ('lighting', 'lighting'),
    # loop_strategy sütunu format adını (ör. seamless_30s) taşır: süre de öğrenilir.
    ('loop_strategy', 'format'),
    ('sound', 'sound'),          # ses stili (sonradan eklendi, eski satırlarda boş: sayılmaz)
]
# Virgülle ayrılmış liste sütunları: listedeki her eleman o videonun skorunu alır.
LISTS = [('materials', 'material'), ('transformations', 'transformation')]

MIN_WEIGHT, MAX_WEIGHT = 0.6, 1.8
PRIOR_STRENGTH = 5          # küçük örneklemlerin aşırı etkili olmasını engeller
# Trend arındırma: hesap büyüdükçe sonraki videolar kendiliğinden daha çok izlenir.
# Ham skor yerine, videonun önceki TREND_WINDOW videonun ortalamasına oranı kullanılır.
TREND_WINDOW, TREND_MIN = 12, 3


def find_due(rows, now_ms, id_field='instagram_id'):
    """24 ve 72 saatlik ölçümü gelmiş videolar. Geç kalan videoda önce 72h, 24h bir sonraki turda.

    `id_field`: platformun video kimliği sütunu. Çıktıda da aynı adla döner."""
    out = []
    for row in rows:
        video_id = str(row.get('video_id') or '')
        if video_id.startswith('TEST_'):
            continue
        if not row.get(id_field) or not row.get('published_at'):
            continue
        published = parse_time_ms(row.get('published_at'))
        if math.isnan(published):
            continue
        age = (now_ms - published) / HOUR_MS
        if age >= 72 and blank(row.get('views_72h')):
            out.append({'video_id': row.get('video_id'), id_field: row.get(id_field), 'snapshot_window': '72h'})
            continue
        if age >= 24 and blank(row.get('views_24h')):
            out.append({'video_id': row.get('video_id'), id_field: row.get(id_field), 'snapshot_window': '24h'})
    return out


def get_metric(entries, name):
    metric = next((e for e in entries if isinstance(e, dict) and e.get('name') == name), None)
    if not metric:
        return 0
    values = metric.get('values')
    if isinstance(values, list) and values and isinstance(values[0], dict) and 'value' in values[0]:
        return tidy(num_or0(values[0]['value']))
    total = metric.get('total_value')
    if isinstance(total, dict) and 'value' in total:
        return tidy(num_or0(total['value']))
    return 0


def parse_metrics(original, main, watch):
    """Instagram yanıtlarından videos satırına yazılacak alanlar.

    Hata döndüyse (silinmiş medya, geçici hata) ölçüm yazılmaz, satır boş kalır ve sonraki turda
    tekrar denenir. Sıfır yazmak videoyu yanlışlıkla "ölçüldü" yapardı."""
    if main.get('error') or not isinstance(main.get('data'), list):
        return {'video_id': original['video_id']}
    data = main['data']
    row = {
        'video_id': original['video_id'],
        'likes': get_metric(data, 'likes'),
        'comments': get_metric(data, 'comments'),
        'shares': get_metric(data, 'shares'),
        'saves': get_metric(data, 'saved'),
    }
    if original['snapshot_window'] == '72h':
        row['views_72h'] = get_metric(data, 'views')
    else:
        row['views_24h'] = get_metric(data, 'views')
    # Ortalama izlenme süresi: Instagram milisaniye verir, saniyeye çevrilir. Alınamazsa yazılmaz.
    watch_ms = get_metric(watch['data'], 'ig_reels_avg_watch_time') if isinstance(watch.get('data'), list) else 0
    if watch_ms > 0:
        row['avg_watch_time'] = tidy(js_round(watch_ms / 10) / 100)
    return row


def ready_to_score(rows, id_field='instagram_id'):
    """72 saatlik görüntülenmesi olan ama skoru henüz hesaplanmamış gerçek videolar."""
    out = []
    for row in rows:
        if str(row.get('video_id') or '').startswith('TEST_') or not row.get('video_id'):
            continue
        if not row.get(id_field):
            continue
        if blank(row.get('views_72h')):
            continue
        if not blank(row.get('performance_score')):
            continue
        out.append(row)
    return out


def performance_score(row):
    views = num_or0(row.get('views_72h'))
    likes = num_or0(row.get('likes'))
    comments = num_or0(row.get('comments'))
    shares = num_or0(row.get('shares'))
    saves = num_or0(row.get('saves'))

    # 1. Erişim: logaritmik ölçek (10 → ~26, 100 → ~50, 1000 → ~75, 10000+ → 100'e yakın)
    reach = min(100.0, 25 * math.log10(views + 1))

    # 2. Etkileşim: share/save daha güçlü sinyal. %20 ağırlıklı etkileşim → yaklaşık 100 puan
    points = likes + comments * 3 + shares * 4 + saves * 4
    rate = points / views if views > 0 else 0
    engagement = min(100.0, rate * 500)

    # 3. Güven: çok az izlenen videonun aşırı etkileşim oranıyla sistemi kandırmasını engeller
    confidence = min(1.0, views / 100)
    adjusted_engagement = engagement * confidence

    # 4. Tutma (ortalama izlenme süresi). Süre, loop_strategy'deki format adından gelir (seamless_30s → 30 sn).
    # Eski "cloudinary_boomerang" satırlarında süre verisi yoktur, skor eski formülle hesaplanır.
    # Yalnız oran kısa videoyu, yalnız mutlak süre uzun videoyu kayırırdı: ikisi karıştırılır.
    # Katsayılar (80, 12 sn) yaklaşık değerlerdir, ~30 video sonra gerçek dağılıma göre ayarlanabilir.
    m = re.search(r'(\d+)s$', str(row.get('loop_strategy') or ''))
    duration = float(m.group(1)) if m else 0.0
    avg_watch = num(row.get('avg_watch_time'))
    has_watch = duration > 0 and finite(avg_watch) and avg_watch > 0

    ratio_score = min(100.0, (avg_watch / duration) * 80) if has_watch else 0.0
    absolute_score = min(100.0, (avg_watch / 12) * 100) if has_watch else 0.0
    # Az izlenen videoda ortalama süre de şansa bağlıdır: etkileşim gibi güvenle ağırlanır
    retention = (0.5 * ratio_score + 0.5 * absolute_score) * confidence

    if has_watch:
        score = reach * 0.45 + adjusted_engagement * 0.30 + retention * 0.25
    else:
        score = reach * 0.65 + adjusted_engagement * 0.35
    return tidy(js_round(score * 100) / 100)


def _mean(values):
    return fold_sum(values) / len(values)


def _genes_of(row):
    """Bir videonun [boyut, değer] çiftleri (aynı çift bir videoda bir kez sayılır)."""
    seen, genes = set(), []

    def add(dimension, value):
        clean = str('' if value is None else value).strip()
        key = f'{dimension}::{clean}'
        if clean == '' or key in seen:
            return
        seen.add(key)
        genes.append((dimension, clean))

    for field, dimension in SINGLE:
        add(dimension, row.get(field))
    for field, dimension in LISTS:
        for part in str('' if row.get(field) is None else row.get(field)).split(', '):
            add(dimension, part)
    add('hook', str('' if row.get('caption') is None else row.get('caption')).split('\n')[0])   # açılış cümlesi
    for tag in captions.tags(row.get('caption')):        # her hashtag ayrı bir gen
        add('tag', tag)
    return genes


def dna_weights(rows, updated_at):
    """Skoru olan gerçek videolardan dna_weights satırları. Öğrenilecek video yoksa boş liste."""
    valid = []
    for row in rows:
        if str(row.get('video_id') or '').startswith('TEST_'):
            continue
        score = row.get('performance_score')
        if score is None or str(score).strip() == '' or not finite(num(score)):
            continue
        valid.append(row)
    if not valid:
        return []

    indexed = []
    for index, row in enumerate(valid):
        t = parse_time_ms(row.get('published_at'))
        indexed.append((math.inf if math.isnan(t) else t, index, row))
    ordered = [row for _, _, row in sorted(indexed, key=lambda e: (e[0], e[1]))]

    raw = [num(r['performance_score']) for r in ordered]
    global_average = _mean(raw)

    relative = []
    for i, score in enumerate(raw):
        previous = raw[max(0, i - TREND_WINDOW):i]
        baseline = _mean(previous) if len(previous) >= TREND_MIN else global_average
        relative.append(score / max(baseline, 1))
    global_relative = _mean(relative)

    groups = {}
    for i, row in enumerate(ordered):
        for dimension, value in _genes_of(row):
            key = f'{dimension}::{value}'
            g = groups.setdefault(key, {'key': key, 'dimension': dimension, 'value': value,
                                        'scores': [], 'relatives': []})
            g['scores'].append(raw[i])
            g['relatives'].append(relative[i])

    out = []
    for g in groups.values():
        n = len(g['scores'])
        avg_score = _mean(g['scores'])
        avg_relative = _mean(g['relatives'])
        # Az verili seçenekleri genel ortalamaya yaklaştır
        adjusted = (n * avg_relative + PRIOR_STRENGTH * global_relative) / (n + PRIOR_STRENGTH)
        weight = adjusted / global_relative if global_relative > 0 else 1.0     # genel ortalama = weight 1
        weight = max(MIN_WEIGHT, min(MAX_WEIGHT, weight))
        out.append({
            'key': g['key'], 'dimension': g['dimension'], 'value': g['value'],
            'avg_score': tidy(js_round(avg_score * 10) / 10),
            'sample_count': n,
            'weight': tidy(js_round(weight * 1000) / 1000),
            'updated_at': updated_at,
        })
    return out
