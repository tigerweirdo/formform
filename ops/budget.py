"""Ücretsiz GitHub Actions dakika bütçesi: sistemin kendi kendini koruması.

Özel depoda ayda 2000 dakika ücretsizdir. Aşılırsa render ve yayın durur. Render bu payın büyük kısmını yer
(1080p ve ses süreyi artırdı), o yüzden her render süresini kaydeder (`config.json` → `usage`) ve her yeni tarif
bütçeye göre seçilir:

  tam        1080p, her süre (8/15/30 sn). Bütçe rahatsa.
  ekonomik   720p, yalnız 8 sn. Bütçe daralınca. Tam bütçe bitse bile sistem durmaz, ucuza döner.

Karar, ayın kalan gününe düşen payla verilir: ay başında bol, ay sonuna doğru tasarruflu. Her formatın maliyeti,
ölçülmüş ortalama varsa ondan (en az 2 render), yoksa tahminden gelir.

Pay, yalnız render'ın değil BÜTÜN workflow'ların harcamasından hesaplanır (`month_total`: GitHub'ın çalışma
listesinden). İlk sürüm yalnız render'ı sayıp gerisinin 500 dakikaya sığacağını varsayıyordu. Canlıda testler beş
günde ~300, yayın/öğrenme/yedek turlar günde ~30 dakika harcadı: bekçi "bütçe rahat" derken ay üçüncü haftada
bitecekti. Artık kalan toplamdan, render dışındaki işlerin ayın sonuna kadarki payı (`OTHER_PER_DAY`) düşülür, kalanı
render'ındır. Toplam okunamazsa son render'ın kaydettiği değer, o da yoksa eski sabit pay (`RENDER_BUDGET`) kullanılır.
"""
import calendar
import math
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import config

MONTH_MINUTES = 2000          # özel depo, ücretsiz plan
RENDER_BUDGET = 1500          # yalnız toplam harcama hiç bilinmiyorsa kullanılan eski sabit render payı
# Render dışındaki her şeyin günlük payı: öğrenme (8 tur), yayın (2 tur, ~2 dk), TikTok (2 tur, ~2 dk), nöbetçi (2 tur),
# panel/jeton/rapor. Ölçülen ~18 dk. Testler buna dahil değil: çalıştıkça toplamdan düşer, render'ın payı kendiliğinden daralır.
OTHER_PER_DAY = 20
SLOTS_PER_DAY = 2             # 06:30 ve 18:30
HEADROOM = 1.4                # kısa render'lar uzunları dengeler: ortalamadan biraz pahalıya izin verilir

# Dakika tahmini: 1080p, GitHub'ın 2 çekirdekli runner'ı, kurulum dahil. İlk gerçek ölçümde (8 sn, 720p: ~4 dk)
# yerelde ölçülen 2,4 kat artışla türetildi. Gerçek ölçümler biriktikçe yerini ölçülen ortalama alır.
DEFAULT_COST = {'seamless_8s': 11.0, 'seamless_15s': 19.0, 'seamless_30s': 29.0}
LITE_FORMAT = 'seamless_8s'
FULL_SIZE = (1080, 1920)
LITE_SIZE = (720, 1280)
KEEP_MONTHS = 3


def month_key(now):
    return now.astimezone(ZoneInfo(config.TIMEZONE)).strftime('%Y-%m')


def _local(now):
    return now.astimezone(ZoneInfo(config.TIMEZONE))


def days_left(now):
    """Bugün dahil, ayın kalan günü."""
    local = _local(now)
    return calendar.monthrange(local.year, local.month)[1] - local.day + 1


def usage_for(cfg, now):
    u = ((cfg or {}).get('usage') or {}).get(month_key(now)) or {}
    out = {'render_min': float(u.get('render_min', 0) or 0), 'renders': int(u.get('renders', 0) or 0),
           'by_format': dict(u.get('by_format') or {})}
    if u.get('all_min') is not None:               # son render'ın kaydettiği, bütün workflow'ların toplamı
        out['all_min'] = float(u['all_min'])
    return out


def month_ranges(now):
    """Ayın bugüne kadarki kısmı, GitHub'ın `created` süzgeci için onar günlük dilimler (UTC tarihleri).

    Süzülmüş çalışma listesi 1000 sonuçta kesilir. On günde 1000 çalışma olmaz, dilimler bunu aşmaz."""
    local = _local(now)
    last = min(local.day, calendar.monthrange(local.year, local.month)[1])
    out = []
    for start in (1, 11, 21):
        if start > last:
            break
        end = last if start == 21 else min(start + 9, last)
        out.append(f'{local.year:04d}-{local.month:02d}-{start:02d}..{local.year:04d}-{local.month:02d}-{end:02d}')
    return out


def billed_minutes(runs, now):
    """Çalışma listesinden faturalanan dakika tahmini. Her iş yukarı yuvarlanır ve en az 1 dakika sayılır.

    Buradaki workflow'ların hepsi tek işlidir, çalışmanın süresi işin süresidir (kuyrukta bekleme birkaç saniye ekler:
    tahmin biraz yüksek kalır, güvenli taraf). Beş günlük gerçek veride iş iş sayılan 608 dakikaya karşı 617 verdi."""
    def at(text):
        try:
            return datetime.fromisoformat(str(text).replace('Z', '+00:00'))
        except ValueError:
            return None
    total = 0
    for run in runs or []:
        if run.get('conclusion') == 'skipped':
            continue
        start = at(run.get('run_started_at') or run.get('created_at'))
        end = at(run.get('updated_at')) if run.get('status') == 'completed' else now
        if start is None or end is None:
            continue
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        total += max(1, math.ceil((end - start).total_seconds() / 60.0))
    return total


def month_total(now, fetch):
    """Bu ay bütün workflow'ların harcadığı dakika. fetch: `github.runs_created` (tarih aralığı → çalışmalar)."""
    runs = []
    for date_range in month_ranges(now):
        runs.extend(fetch(date_range))
    return billed_minutes(runs, now)


def cost(usage, fmt):
    """Bir formatın render maliyeti (dakika): ölçülmüş ortalama (en az 2 örnek) ya da tahmin."""
    measured = usage['by_format'].get(fmt) or {}
    if int(measured.get('n', 0) or 0) >= 2:
        return float(measured['min']) / int(measured['n'])
    return DEFAULT_COST.get(fmt, 20.0)


def decide(cfg, now, formats, used_total=None):
    """Bu tur için izin verilen formatlar ve çözünürlük. formats: evrim motorunun format adları.

    used_total: bu ay bütün workflow'ların harcadığı dakika (`month_total`). Verilmezse son render'ın kaydettiği
    değere (`all_min`), o da yoksa yalnız render'ı sayan eski paya düşülür."""
    u = usage_for(cfg, now)
    if used_total is None:
        used_total = u.get('all_min')
    days = days_left(now)
    if used_total is None:
        remaining = max(0.0, RENDER_BUDGET - u['render_min'])
    else:                                           # kalan toplamdan diğer işlerin ay sonuna kadarki payı düşülür
        used_total = max(float(used_total), u['render_min'])     # liste eksik gelse de toplam, kayıtlı render'dan az olamaz
        remaining = max(0.0, MONTH_MINUTES - used_total - days * OTHER_PER_DAY)
    allowance = remaining / max(1, days * SLOTS_PER_DAY)
    limit = min(allowance * HEADROOM, remaining)
    allowed = [f for f in formats if cost(u, f) <= limit]
    if allowed:
        level, size = 'full', FULL_SIZE
    else:
        level, size, allowed = 'lite', LITE_SIZE, [LITE_FORMAT]
    return {'level': level, 'formats': allowed, 'width': size[0], 'height': size[1], 'used': u['render_min'],
            'used_total': used_total, 'remaining': remaining, 'budget': RENDER_BUDGET, 'allowance': allowance}


def record(cfg, now, minutes, fmt=None, used_total=None):
    """Bir render çalışmasının dakikasını ekler. cfg değiştirilir (Store.transaction içinde çağrılır).

    used_total verilirse bütün workflow'ların ay içi toplamı da yazılır (`all_min`): haftalık özet ve GitHub'ın
    okunamadığı bir sonraki tur bunu kullanır."""
    usage = cfg.setdefault('usage', {})
    month = usage.setdefault(month_key(now), {})
    if used_total is not None:
        month['all_min'] = round(float(used_total), 2)
    month['render_min'] = round(float(month.get('render_min', 0) or 0) + float(minutes), 2)
    month['renders'] = int(month.get('renders', 0) or 0) + 1
    if fmt:
        by = month.setdefault('by_format', {})
        entry = by.setdefault(fmt, {'n': 0, 'min': 0.0})
        entry['n'] = int(entry['n']) + 1
        entry['min'] = round(float(entry['min']) + float(minutes), 2)
    for old in sorted(usage)[:-KEEP_MONTHS]:          # eski ayları unut
        del usage[old]
    return month


LEVEL_TEXT = {'full': 'tam (1080p, her süre)', 'lite': 'ekonomik (720p, yalnız 8 sn)'}


def summary(cfg, now):
    d = decide(cfg, now, list(DEFAULT_COST))
    return {'used': d['used'], 'budget': d['budget'], 'remaining': d['remaining'], 'used_total': d['used_total'],
            'level': d['level'], 'level_text': LEVEL_TEXT[d['level']]}
