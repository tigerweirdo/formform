"""Haftalık özet: sistem ne yaptı, ne öğrendi, sağlıklı mı.

Eski n8n REPORT workflow'undaki 'BUILD REPORT' düğümünün birebir karşılığıdır.
"""
import math
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import config
from .budget import MONTH_MINUTES
from .jsnum import blank, finite, fold_sum, num, parse_time_ms, to_fixed

HOUR_MS = 60 * 60 * 1000
DAY_MS = 24 * HOUR_MS

NAMES = {
    'structure': 'Yapı', 'palette': 'Palet', 'motion': 'Hareket', 'lighting': 'Işık', 'format': 'Süre',
    'material': 'Malzeme', 'transformation': 'Dönüşüm', 'hook': 'Açılış cümlesi', 'sound': 'Ses',
    'tag': 'Hashtag',
}


def _is_num(v):
    return not blank(v) and finite(num(v))


def _mean(a):
    return fold_sum(a) / len(a) if a else math.nan


def _median(a):
    if not a:
        return math.nan
    s = sorted(a)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2


def _f1(v):
    return to_fixed(v, 1) if finite(v) else '-'


def _f2(v):
    return to_fixed(v, 2) if finite(v) else '-'


def _hour_of(iso):
    ms = parse_time_ms(iso)
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(ZoneInfo(config.TIMEZONE)).hour


def build_report(videos, weights, now_ms, usage=None):
    """usage: budget.summary() (verilirse sağlık bölümüne Actions dakikası satırları eklenir)."""
    videos = [r for r in videos if r.get('video_id') and not str(r['video_id']).startswith('TEST_')]
    weights = [w for w in weights if w.get('dimension') and w.get('value')]

    def time(r):
        return parse_time_ms(r.get('published_at'))

    published = sorted([r for r in videos if not math.isnan(time(r))], key=time)
    scored = [r for r in published if _is_num(r.get('performance_score'))]

    lines = []
    out = lines.append

    # ---------------------------------------------------------------- 1. ÖZET
    posted7 = len([r for r in published if now_ms - time(r) <= 7 * DAY_MS])
    last_n = scored[-7:]
    prev_n = scored[max(0, len(scored) - 14):max(0, len(scored) - 7)]

    out('FORMFORM haftalık özet')
    out('')
    out('1. ÖZET')
    out(f'- Son 7 günde yayınlanan: {posted7} video (beklenen 14)')
    out(f'- Toplam yayınlanan: {len(published)}, skorlanan: {len(scored)}')

    if not scored:
        out('- Henüz skorlanmış video yok. İlk skorlar yayından yaklaşık 3 gün sonra gelir.')
    else:
        out(f"- Son {len(last_n)} skorlu videonun ortalaması: {_f1(_mean([num(r['performance_score']) for r in last_n]))}")
        if len(prev_n) >= 3:
            a = _mean([num(r['performance_score']) for r in last_n])
            b = _mean([num(r['performance_score']) for r in prev_n])
            change = ((a - b) / max(b, 1)) * 100
            out(f"- Önceki {len(prev_n)} videoya göre: {'+' if change >= 0 else ''}{to_fixed(change, 0)}% "
                f"({'yükseliyor' if change >= 0 else 'düşüyor'})")

    # ---------------------------------------------------------------- 2. SON VİDEOLAR
    def describe(r):
        watch = f", izlenme {_f1(num(r['avg_watch_time']))} sn" if _is_num(r.get('avg_watch_time')) else ''
        views = f", {int(num(r['views_72h']))} görüntülenme" if _is_num(r.get('views_72h')) else ''
        return (f"{r['video_id']}: skor {_f1(num(r['performance_score']))} | "
                f"{r.get('structure', '')}, {r.get('palette', '')}, {r.get('loop_strategy', '')}{views}{watch}")

    if scored:
        out('')
        out('2. SON SKORLANAN VİDEOLAR')
        for r in reversed(scored[-5:]):
            out(f'- {describe(r)}')

    if len(scored) >= 6:
        by_score = sorted(scored, key=lambda r: -num(r['performance_score']))
        out('')
        out('3. EN İYİ 3 VE EN KÖTÜ 3 (tüm zamanlar)')
        out('En iyi:')
        for r in by_score[:3]:
            out(f'- {describe(r)}')
        out('En kötü:')
        for r in reversed(by_score[-3:]):
            out(f'- {describe(r)}')

    # ---------------------------------------------------------------- 4. FORMAT
    by_format = {}
    for r in scored:
        by_format.setdefault(str(r.get('loop_strategy') or 'bilinmiyor'), []).append(r)

    if by_format:
        import re
        out('')
        out('4. SÜRE / FORMAT')
        for fmt in sorted(by_format):
            rs = by_format[fmt]
            m = re.search(r'(\d+)s$', fmt)
            ratios = ([num(r['avg_watch_time']) / float(m.group(1)) for r in rs
                       if _is_num(r.get('avg_watch_time')) and num(r['avg_watch_time']) > 0] if m else [])
            watch = f", medyan izlenen oran %{to_fixed(_median(ratios) * 100, 0)}" if ratios else ''
            out(f"- {fmt}: {len(rs)} video, ortalama skor {_f1(_mean([num(r['performance_score']) for r in rs]))}{watch}")
        if any(len(rs) < 8 for rs in by_format.values()):
            out('  (Bazı formatlarda 8’den az video var, kıyaslama henüz güvenilir değil.)')

    # ---------------------------------------------------------------- 5. SAAT
    morning = [r for r in scored if _hour_of(r['published_at']) < 14]
    evening = [r for r in scored if _hour_of(r['published_at']) >= 14]

    if morning and evening:
        am = _mean([num(r['performance_score']) for r in morning])
        pm = _mean([num(r['performance_score']) for r in evening])
        gap = ((am - pm) / max(min(am, pm), 1)) * 100
        out('')
        out('5. YAYIN SAATİ')
        out(f'- Sabah (08:00): {len(morning)} video, ortalama skor {_f1(am)}')
        out(f'- Akşam (20:00): {len(evening)} video, ortalama skor {_f1(pm)}')
        if min(len(morning), len(evening)) < 10:
            out('- Her saatte en az 10 video olmadan fark güvenilir değil.')
        elif abs(gap) < 10:
            out('- Fark %10’dan küçük: saatler arasında anlamlı fark yok.')
        else:
            out(f"- {'Sabah' if am > pm else 'Akşam'} yayınları %{to_fixed(abs(gap), 0)} daha iyi. "
                'İstersen yalnız o saati bırakıp diğerini kaldırabiliriz.')

    # ---------------------------------------------------------------- 6. ÖĞRENİLENLER
    learned = [w for w in weights if num(w.get('sample_count')) >= 3 and finite(num(w.get('weight')))]
    if learned:
        out('')
        out('6. SİSTEMİN ÖĞRENDİKLERİ (ağırlık 1 = nötr, en az 3 örnek)')
        for dim in NAMES:
            rows = sorted([w for w in learned if w['dimension'] == dim], key=lambda w: -num(w['weight']))
            if not rows:
                continue

            def fmt_w(w):
                return f"{w['value']} ({_f2(num(w['weight']))}, n={w['sample_count']})"

            best = '; '.join(fmt_w(w) for w in rows[:2])
            worst = f' | en zayıf: {fmt_w(rows[-1])}' if len(rows) > 2 else ''
            out(f'- {NAMES[dim]}: en iyi {best}{worst}')

    # ---------------------------------------------------------------- 7. SAĞLIK
    out('')
    out('7. SİSTEM SAĞLIĞI')
    alerts = []

    if published:
        last = published[-1]
        hours_ago = (now_ms - time(last)) / HOUR_MS
        out(f"- Son paylaşım: {to_fixed(hours_ago, 0)} saat önce ({last['video_id']})")
        if hours_ago > 36:
            alerts.append(f'Son paylaşımdan {to_fixed(hours_ago, 0)} saat geçti, beklenen en çok 12. '
                          'Yayın durmuş olabilir. GitHub → Actions sekmesinde "FORMFORM publish" ve '
                          '"FORMFORM render" çalışmalarına bakın.')
    else:
        alerts.append('Hiç yayınlanmış video yok.')

    if usage and usage.get('used_total') is not None:        # bütün workflow'ların toplamı biliniyor
        out(f"- Bu ay Actions dakikası: toplam {to_fixed(usage['used_total'], 0)} / {MONTH_MINUTES} "
            f"(render {to_fixed(usage['used'], 0)}). Render’a kalan pay {to_fixed(usage['remaining'], 0)}, "
            f"kalite seviyesi: {usage['level_text']}")
        if usage['used_total'] > 0.85 * MONTH_MINUTES:
            alerts.append('Bu ayki Actions dakikası ücretsiz hakkın %85’ini aştı. Sistem kendiliğinden ekonomik moda '
                          '(720p, 8 sn) geçer, yeni aya kadar durmaz.')
    elif usage:
        out(f"- Bu ay render dakikası: {to_fixed(usage['used'], 0)} / {usage['budget']} "
            f"(kalan {to_fixed(usage['remaining'], 0)}), kalite seviyesi: {usage['level_text']}")
        if usage['used'] > 0.85 * usage['budget']:
            alerts.append('Bu ayki render dakikası bütçenin %85’ini aştı. Sistem kendiliğinden ekonomik moda '
                          '(720p, 8 sn) geçer, yeni aya kadar durmaz.')

    pending = len([r for r in published if now_ms - time(r) > 80 * HOUR_MS and not _is_num(r.get('views_72h'))])
    no_id = len([r for r in videos if not r.get('instagram_id')])

    if pending > 0:
        alerts.append(f'{pending} videonun 72 saatlik ölçümü hâlâ boş (80 saatten eski). '
                      'Öğrenme akışı ve Instagram jetonu kontrol edilmeli.')
    if no_id > 0:
        alerts.append(f'{no_id} satırda instagram_id yok.')

    if not alerts:
        out('- Sorun görünmüyor.')
    for a in alerts:
        out(f'- DİKKAT: {a}')

    with_watch = len([r for r in scored if _is_num(r.get('avg_watch_time')) and num(r['avg_watch_time']) > 0])
    if scored:
        out(f'- İzlenme süresi verisi olan skorlu video: {with_watch}/{len(scored)}')

    # ---------------------------------------------------------------- 8. KATSAYI AYARI
    if len(scored) >= 30:
        out('')
        out('8. SKOR KATSAYILARINI AYARLAMA ZAMANI')
        out(f'{len(scored)} skorlu video var. İzlenme süresi katsayıları (oran çarpanı 80, mutlak süre tavanı 12 sn, '
            'ağırlık %25) yaklaşık değerlerle başlamıştı.')
        out('Claude’a (bu projenin oturumuna) "skor katsayılarını gerçek verilerle ayarla" demen yeterli. '
            'Bu raporu da ekle.')

    recent = _f1(_mean([num(r['performance_score']) for r in last_n])) if last_n else 'veri yok'
    return {
        'subject': f'[FORMFORM] Haftalık özet: {posted7} video yayınlandı, skor ortalaması {recent}',
        'message': '\n'.join(lines),
        'posted7': posted7,
        'scored': len(scored),
    }
