"""Takvim: her işin Berlin duvar saati ve onun UTC cron karşılığı.

Europe/Berlin kışın UTC+1 (CET), yazın UTC+2 (CEST) olur, cron ise yaz saatini bilmez. Duvar saati yıl boyu aynı
kalsın diye her işin iki cron ifadesi vardır (yaz ve kış), o anki ofsetinki geçerlidir.

Turları saat (Cloudflare Worker, `dashboard/clock.js`) başlatır. Render, yayın, TikTok ve öğrenmenin GitHub'da kendi
zamanlayıcısı yoktur: buradaki cron ifadeleri onlar için yalnız takvimdir (panel ve nöbetçi yuvaları bundan hesaplar).
Saat bir yuvayı kaçırırsa nöbetçi (`watch.yml`, `ops/watch.py`) günde iki kez bakıp başlatır.

Jeton ve haftalık özet seyrek çalışır, GitHub'daki kendi iki cron'unu yedek olarak korur: tetiklenen cron o anki
ofsetin cron'u değilse çalışma iş yapmadan başarılı biter (`ops season`), saat o yuvayı aldıysa da (`ops slot`).

Öğrenme üç saatte bir çalışır; bu bir duvar saati değil, tek ifade yeter.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Europe/Berlin')

# (cron, utc ofset saati). Ofset 2 yaz, 1 kış. None: ofsete bağlı değil.
JOBS = {
    'render': {
        'local': ('06:30', '18:30'),
        'crons': (('30 4,16 * * *', 2), ('30 5,17 * * *', 1)),
    },
    'publish': {
        'local': ('07:45', '19:45'),
        'crons': (('45 5,17 * * *', 2), ('45 6,18 * * *', 1)),
    },
    # TikTok taslağı Instagram yayınından 5 dk sonra gelen kutusuna düşer: kullanıcı ~08:00 / ~20:00'de paylaşır.
    'tiktok': {
        'local': ('07:50', '19:50'),
        'crons': (('50 5,17 * * *', 2), ('50 6,18 * * *', 1)),
    },
    'token': {
        'local': ('05:07',),
        'crons': (('7 3 1,15 * *', 2), ('7 4 1,15 * *', 1)),
    },
    'report': {
        'local': ('09:17',),
        'crons': (('17 7 * * 1', 2), ('17 8 * * 1', 1)),
    },
    'learn': {
        'local': (),
        'crons': (('5 */3 * * *', None),),
    },
    # Nöbetçi: UTC'de sabit. Berlin'de yazın 09:25 / 21:25, kışın 08:25 / 20:25: her mevsimde TikTok yuvasından
    # (07:50 / 19:50) en az WATCH_GRACE_MIN sonra. GitHub bu turu geciktirse de olur: nöbetçi saate değil duruma bakar.
    'watch': {
        'local': (),
        'crons': (('25 7,19 * * *', None),),
    },
}

# Nöbetçinin baktığı işler (GitHub'da kendi zamanlayıcısı olmayanlar), başlatma sırasıyla.
WATCHED = ('render', 'publish', 'tiktok', 'learn')
WATCH_GRACE_MIN = 30     # saatin kendi penceresi 20 dk: yuva ancak bundan sonra "kaçtı" sayılır
WATCH_MAX_AGE_H = 8      # bundan eski yuva başlatılmaz: 12 saat arayla gelen sıradaki yuva onu zaten yapacak
FAILED = ('cancelled', 'failure', 'timed_out', 'startup_failure')


def offset_hours(now):
    """Europe/Berlin’in o andaki UTC ofseti, tam saat (1 veya 2)."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return int(now.astimezone(TZ).utcoffset().total_seconds() // 3600)


def cron_strings(job):
    return [cron for cron, _offset in JOBS[job]['crons']]


def local_times(job):
    return list(JOBS[job]['local'])


def should_run(job, event, schedule, now):
    """Elle çalıştırma her zaman iş yapar. Zamanlı tur yalnız bu mevsimin cron’uysa."""
    if event != 'schedule':
        return True
    rows = JOBS[job]['crons']
    if rows[0][1] is None:
        return True
    off = offset_hours(now)
    fired = (schedule or '').strip()
    return any(cron == fired and offset == off for cron, offset in rows)


def _field_has(field, value):
    if field == '*':
        return True
    if field.startswith('*/'):
        return value % int(field[2:]) == 0
    return value in {int(part) for part in field.split(',')}


def matches_cron(cron, moment):
    """UTC anı bu cron ifadesine denk mi. Gün: 0 ve 7 pazar, 1 pazartesi."""
    minute, hour, dom, mon, dow = cron.split()
    if not all((_field_has(minute, moment.minute), _field_has(hour, moment.hour),
                _field_has(dom, moment.day), _field_has(mon, moment.month))):
        return False
    if dow == '*':
        return True
    cron_dow = (moment.weekday() + 1) % 7
    allowed = {0 if int(part) == 7 else int(part) for part in dow.split(',')}
    return cron_dow in allowed


def latest_occurrence(cron, now):
    """now'dan geriye, bu cron'un en son ateşlenme anı (UTC, dakika). Yoksa None."""
    if not cron or len(cron.split()) != 5:
        return None
    from datetime import timedelta
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    cursor = now.astimezone(timezone.utc).replace(second=0, microsecond=0)
    for _ in range(36 * 60):
        if cursor <= now and matches_cron(cron, cursor):
            return cursor
        cursor -= timedelta(minutes=1)
    return None


def latest_slot(job, now, grace_minutes=WATCH_GRACE_MIN):
    """İşin, üstünden en az `grace_minutes` geçmiş en son yuvası (UTC). O anki Berlin ofsetinin ifadesi kullanılır."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    moment = now - timedelta(minutes=grace_minutes)
    rows = JOBS[job]['crons']
    if rows[0][1] is None:
        cron = rows[0][0]
    else:
        cron = next(c for c, offset in rows if offset == offset_hours(moment))
    return latest_occurrence(cron, moment)


def _created(run):
    raw = run.get('created_at') or run.get('createdAt') or ''
    try:
        created = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
    except ValueError:
        return None
    return created if created.tzinfo else created.replace(tzinfo=timezone.utc)


def started_since(runs, slot):
    """Bu yuvadan beri (2 dk pay) açılmış çalışmalar: saat, nöbetçi ya da elle, fark etmez."""
    floor = slot - timedelta(minutes=2)
    return [r for r in runs or [] if _created(r) is not None and _created(r) >= floor]


def covered(runs, slot):
    """Yuva dolu mu: o yuvadan beri açılmış, başarısız olmamış (bitmiş ya da hâlâ süren) bir çalışma var mı.

    Saatin `alreadyCovered` kuralıyla aynıdır: başarısız çalışma yuvayı kapatmaz, yeniden denenebilir."""
    return any(r.get('conclusion') not in FAILED for r in started_since(runs, slot))


def clock_run_covers(runs, slot):
    """Saat turu bu yuvayı zaten aldıysa GitHub'ın geç gelen yedeği iş yapmasın.

    Başarısız saat turu yuvayı kapatmaz: yedek o işi yapabilir.
    """
    from datetime import timedelta
    if slot is None:
        return False
    floor = slot - timedelta(minutes=2)
    for run in runs or []:
        title = str(run.get('displayTitle') or run.get('display_title') or '')
        if '(saat)' not in title:
            continue
        raw = run.get('createdAt') or run.get('created_at') or ''
        try:
            created = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        except ValueError:
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if created < floor:
            continue
        if run.get('conclusion') in ('cancelled', 'failure', 'timed_out', 'startup_failure'):
            continue
        return True
    return False
