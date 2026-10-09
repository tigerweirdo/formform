"""TikTok ÖĞRENME: paylaşılan taslakları bulur, 24/72 saatlik sayıları okur, skoru ve ağırlıkları yazar.

Taslak modunda paylaşımı kullanıcı yapar. Yükleme anında ne paylaşım zamanı ne de TikTok'un video kimliği bilinir,
satır `drafted` kalır ve öğrenmeye girmez. Bu adım ikisini sonradan bulur:

  1. `drafted` satırlar hesabın videolarıyla eşleştirilir, eşleşen satır `published` olur (gerçek paylaşım zamanı
     ve TikTok video kimliği yazılır, yükleme kimliği `publish_id` sütununa taşınır)
  2. paylaşımdan 24 ve 72 saat geçen videoların sayıları okunur (izlenme, beğeni, yorum, paylaşım)
  3. 72 saatlik verisi olan videolara performans skoru, skorlu videolardan `tiktok/dna_weights.csv`

TikTok kaydetme sayısı ve izlenme süresi vermez: skor eski formüle düşer (%65 erişim, %35 etkileşim).
Yalnız okuma çağrıları yapılır, TikTok'ta hiçbir şey değişmez.

Eşleştirme sırayla üç kanıta bakar ve yalnız TEK aday varsa karar verir. Yanlış eşleşme bir videonun başarısını
başka bir tarife yazardı (öğrenmeyi bozar), eşleşmeyen satır ise yalnızca bir tur daha bekler:
  - kesin: TikTok'un durum yanıtı bu yüklemenin dönüştüğü video kimliğini verir (`publicaly_available_post_id`)
  - başlık: videonun metninde bu taslağın açılış cümlesi geçer (kullanıcı başlığı yapıştırmışsa)
  - süre ve zaman: render'dan sonra paylaşılmış, süresi formatla çelişmeyen tek video (başlık yapıştırılmamışsa)
"""
import math
import re
from datetime import datetime, timezone

from . import caption as captions
from . import config, learn, ledger, net, scoring, tiktok
from .jsnum import finite, num, num_or0, parse_time_ms, tidy

PLATFORM = 'tiktok'
ID_FIELD = 'tiktok_id'
DURATION_SLACK = 1.5        # sn: TikTok süreyi tam saniyeye yuvarlar
DAY_MS = 24 * 60 * 60 * 1000

EXACT, BY_CAPTION, BY_TIME = 'TikTok bildirdi', 'başlıktan', 'süre ve zamandan'


def _text(value):
    return ' '.join(str('' if value is None else value).casefold().split())


def _seconds(row):
    m = re.search(r'(\d+)s$', str(row.get('loop_strategy') or ''))
    return float(m.group(1)) if m else 0.0


def _posted_ms(video):
    try:
        return int(video.get('create_time') or 0) * 1000
    except (TypeError, ValueError):
        return 0


def _fits(row, video):
    """Bu video bu taslaktan çıkmış olabilir mi: render'dan sonra paylaşılmış ve süresi formatla çelişmiyor."""
    posted = _posted_ms(video)
    if not posted:
        return False
    rendered = parse_time_ms(row.get('created_at'))
    if not math.isnan(rendered) and posted < rendered:
        return False
    want, got = _seconds(row), num(video.get('duration'))
    return not (want and finite(got) and got > 0 and abs(got - want) > DURATION_SLACK)


def _same_caption(row, video):
    hook = _text(captions.hook(row.get('caption')))
    return bool(hook) and hook in _text(f"{video.get('title') or ''} {video.get('video_description') or ''}")


def _only_pairs(rows, videos, fits):
    """İki yönden de tek olan eşleşmeler: satırın tek adayı o video, o videonun tek adayı da o satır."""
    candidates = {r['video_id']: [v for v in videos if fits(r, v)] for r in rows}
    out = []
    for row in rows:
        mine = candidates[row['video_id']]
        if len(mine) != 1:
            continue
        rivals = [r for r in rows if any(v is mine[0] for v in candidates[r['video_id']])]
        if len(rivals) == 1:
            out.append((row, mine[0]))
    return out


def match_posts(drafts, videos, statuses, taken):
    """`drafted` satırları hesabın videolarıyla eşleştirir. [(satır, video, kanıt)] döner.

    drafts: eşleşmesi aranan satırlar. videos: hesabın videoları (`tiktok.list_videos`). statuses: {video_id: TikTok
    durum yanıtı ya da None}. taken: başka satırlara zaten bağlı TikTok video kimlikleri (bir video iki tarife yazılmaz)."""
    free = [v for v in videos if v.get('id') and str(v['id']) not in taken]
    left = list(drafts)
    out = []

    def settle(row, video, proof):
        out.append((row, video, proof))
        left.remove(row)
        free.remove(video)

    for row in list(left):
        posted = (statuses.get(row['video_id']) or {}).get('publicaly_available_post_id') or []
        video = next((v for v in free if str(v['id']) in {str(p) for p in posted}), None)
        if video is not None:
            settle(row, video, EXACT)
    for proof, fits in ((BY_CAPTION, lambda r, v: _fits(r, v) and _same_caption(r, v)), (BY_TIME, _fits)):
        for row, video in _only_pairs(left, free, fits):
            settle(row, video, proof)
    return out


def _published(row, video):
    """Eşleşen satıra yazılacak alanlar: gerçek paylaşım zamanı ve TikTok'un video kimliği."""
    posted = datetime.fromtimestamp(_posted_ms(video) / 1000, timezone.utc)
    return {ID_FIELD: str(video['id']), 'publish_id': row.get(ID_FIELD) or '', 'published_at': ledger.stamp(posted),
            'status': 'published'}


def _count(video, name):
    return tidy(num_or0(video.get(name)))


def _measurement(item, video):
    row = {'likes': _count(video, 'like_count'), 'comments': _count(video, 'comment_count'),
           'shares': _count(video, 'share_count')}
    row['views_72h' if item['snapshot_window'] == '72h' else 'views_24h'] = _count(video, 'view_count')
    return row


def _watched(row, now_ms):
    """Taslak hâlâ aranıyor mu: çok eski taslak (kullanıcı hiç paylaşmamış) her turda TikTok'a sorulmaz."""
    rendered = parse_time_ms(row.get('created_at'))
    return math.isnan(rendered) or now_ms - rendered <= config.TIKTOK_DRAFT_WATCH_DAYS * DAY_MS


def run(store, token, now, dry_run=False, log=print):
    now_ms = now.timestamp() * 1000
    summary = {'matched': 0, 'measured': 0, 'scored': 0, 'weights': 0}
    rows = store.read().ledger(PLATFORM).videos
    drafts = [r for r in rows if r.get('status') == 'drafted' and r.get(ID_FIELD) and r.get('video_id')
              and _watched(r, now_ms)]
    if not drafts and not scoring.find_due(rows, now_ms, id_field=ID_FIELD):
        log('TikTok: aranacak taslak ya da zamanı gelen ölçüm yok.')
        return summary

    videos = tiktok.list_videos(token, config.TIKTOK_SYNC_VIDEOS)
    by_id = {str(v.get('id')): v for v in videos if v.get('id')}
    log(f'TikTok: hesabın {len(videos)} videosu okundu, {len(drafts)} taslak aranıyor.')

    statuses = {}
    for row in drafts:
        try:
            statuses[row['video_id']] = tiktok.publish_status(token, row[ID_FIELD])
        except net.HttpError as e:                     # durum alınamazsa başlık ve süre kanıtı yine denenir
            statuses[row['video_id']] = None
            log(f"Uyarı: {row['video_id']} için TikTok yükleme durumu alınamadı: {e}")

    taken = {str(r.get(ID_FIELD)) for r in rows if r.get('status') == 'published' and r.get(ID_FIELD)}
    matches = {}
    for row, video, proof in match_posts(drafts, videos, statuses, taken):
        matches[row['video_id']] = _published(row, video)
        log(f"Paylaşılmış: {row['video_id']} → TikTok {video['id']} ({matches[row['video_id']]['published_at']}, {proof})")
    for row in drafts:
        if row['video_id'] not in matches:
            state = (statuses.get(row['video_id']) or {}).get('status') or 'bilinmiyor'
            log(f"Henüz paylaşılmamış ya da eşleşmedi: {row['video_id']} (TikTok durumu: {state})")

    # Yeni eşleşen video da aynı turda ölçülür (paylaşımının üstünden 24 saat geçmişse)
    current = [dict(r, **matches[r['video_id']]) if r.get('video_id') in matches else r for r in rows]
    measures = {}
    for item in scoring.find_due(current, now_ms, id_field=ID_FIELD):
        video = by_id.get(str(item[ID_FIELD]))
        if video is None:                              # silinmiş, gizlenmiş ya da listenin dışında: sonraki turda yine denenir
            log(f"Ölçüm alınamadı: {item['video_id']} ({item['snapshot_window']}), video TikTok listesinde yok")
            continue
        measures[item['video_id']] = _measurement(item, video)
        log(f"Ölçüm: {item['video_id']} {item['snapshot_window']} (izlenme {_count(video, 'view_count')})")

    if not matches and not measures:
        log('TikTok: yazılacak yeni bir şey yok.')
        return summary
    if dry_run:
        log(f'DENEME: kayıt YAZILMADI. {len(matches)} paylaşım bulundu, {len(measures)} ölçüm alınacaktı.')
        return dict(summary, matched=len(matches), measured=len(measures), dry_run=True)

    stamp = ledger.stamp(now)

    def apply(data):
        book = data.ledger(PLATFORM)
        index = {r.get('video_id'): r for r in book.videos}
        matched = measured = scored = 0
        for video_id, fields in matches.items():
            row = index.get(video_id)
            if row is not None and row.get('status') == 'drafted':          # araya başka bir tur girdiyse dokunma
                row.update(fields)
                matched += 1
        for video_id, fields in measures.items():
            row = index.get(video_id)
            if row is not None and row.get('status') == 'published':
                row.update(fields)
                measured += 1
        for row in scoring.ready_to_score(book.videos, id_field=ID_FIELD):
            row['performance_score'] = scoring.performance_score(row)
            scored += 1
        weights = scoring.dna_weights(book.videos, stamp)
        learn.merge_weights(book.dna, weights)
        summary.update(matched=matched, measured=measured, scored=scored, weights=len(weights))

    store.transaction(apply, 'TikTok öğrenme: paylaşımlar, ölçümler ve skorlar')
    log(f"Tamam: {summary['matched']} paylaşım bulundu, {summary['measured']} ölçüm, {summary['scored']} yeni skor, "
        f"{summary['weights']} ağırlık.")
    return summary
