"""ÖĞRENME: 24/72 saatlik ölçümleri toplar, performans skorunu hesaplar, DNA ağırlıklarını günceller.

Eski n8n LEARNING workflow'unun karşılığı. Üç adım tek sırayla:
  1. 24s / 72s ölçümü gelen videoların Instagram verisi
  2. 72 saatlik verisi olup skoru olmayan videolara performans skoru
  3. Skoru olan tüm videolardan dna_weights

Ölçülecek video ya da yeni skor yoksa sonraki adım yine çalışır.
"""
from datetime import datetime, timezone

from . import instagram, scoring, tokens
from .errors import OpsError, TokenMissing

METRICS = 'views,likes,comments,shares,saved'
WATCH_METRIC = 'ig_reels_avg_watch_time'


def merge_weights(dna, weights):
    """Yeni hesaplanan ağırlıkları kayıttaki satırların üstüne yazar, olmayanı ekler (sıra korunur)."""
    index = {w['key']: w for w in dna}
    for w in weights:
        if w['key'] in index:
            index[w['key']].update(w)
        else:
            dna.append(dict(w))
            index[w['key']] = dna[-1]


def run(store, clock=None, log=print):
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    now_ms = now.timestamp() * 1000
    stamp = now.strftime('%Y-%m-%dT%H:%M:%S.') + f'{now.microsecond // 1000:03d}Z'

    data = store.read()
    due = scoring.find_due(data.videos, now_ms)
    problem = None
    updates = []

    token = None
    if due:
        try:
            token = tokens.current(data)
        except TokenMissing as e:
            problem = e                     # skor ve ağırlıklar yine hesaplanır, çalışma sonunda kırmızı biter

    failed = 0
    for item in (due if token else []):
        main = instagram.insights(token, item['instagram_id'], METRICS)
        watch = instagram.insights(token, item['instagram_id'], WATCH_METRIC)
        row = scoring.parse_metrics(item, main, watch)
        if len(row) == 1:                  # yalnız video_id: Instagram hata verdi, sonraki turda tekrar denenir
            failed += 1
            log(f"Ölçüm alınamadı: {item['video_id']} ({item['snapshot_window']})")
            continue
        updates.append(row)
        log(f"Ölçüm: {item['video_id']} {item['snapshot_window']}")

    summary = {}

    def apply(d):
        by_id = {r.get('video_id'): r for r in d.videos}
        for upd in updates:
            row = by_id.get(upd['video_id'])
            if row is not None:
                row.update({k: v for k, v in upd.items() if k != 'video_id'})

        scored = 0
        for row in scoring.ready_to_score(d.videos):
            row['performance_score'] = scoring.performance_score(row)
            scored += 1

        weights = scoring.dna_weights(d.videos, stamp)
        merge_weights(d.dna, weights)
        summary.update(measured=len(updates), scored=scored, weights=len(weights))

    store.transaction(apply, 'Öğrenme: ölçümler, skorlar ve ağırlıklar')
    log(f"Tamam: {summary['measured']} ölçüm, {summary['scored']} yeni skor, {summary['weights']} ağırlık.")

    if problem:
        raise problem
    if due and failed == len(due):
        raise OpsError(f'Instagram ölçüm çağrıları başarısız oldu ({failed}/{len(due)} video).',
                       hint='Instagram jetonunun süresi dolmuş ya da izinleri eksik olabilir. Deponun Settings → '
                            'Secrets sayfasında IG_ACCESS_TOKEN değerini yenileyin. Silinmiş videoların ölçümü '
                            'her turda yeniden denenir.')
    return summary
