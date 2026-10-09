"""Komut satırı girişi: python -m ops <komut>. GitHub Actions workflow'ları bunları çağırır."""
import argparse
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone

from . import (alerts, budget, evolve, github, learn, net, platforms, publish, report, schedule, tiktok, tiktok_sync,
               tokens, watch)
from .errors import CredentialsMissing, OpsError, TiktokNotConnected
from .store import Store


def _month_total(now):
    """Bu ay bütün workflow'ların harcadığı dakika (GitHub'dan). Okunamazsa None: bekçi son bilinen değerle karar verir."""
    try:
        return budget.month_total(now, github.runs_created)
    except (net.HttpError, OpsError) as e:
        print(f'Uyarı: ay içi toplam Actions dakikası okunamadı, son bilinen değerle devam: {e}')
        return None


def _plan(args):
    out = args.out
    os.makedirs(out, exist_ok=True)
    spec_text = os.environ.get(args.spec_env or '', '').strip() if args.spec_env else ''
    if spec_text:                                        # elle verilen tarif (deneme)
        spec = json.loads(spec_text)
        if not re.fullmatch(r'[A-Za-z0-9_-]{3,64}', str(spec.get('video_id', ''))):
            raise OpsError('Geçersiz video_id (harf, rakam, _ ve - kullanın, 3-64 karakter)')
        payload = spec
        print(f'Elle verilen tarif kullanılıyor: {payload["video_id"]}')
    else:
        data = Store.from_env().read()
        now = datetime.now(timezone.utc)
        # Ücretsiz dakika bütçesi: seviye ve formatlar. Yalnız render değil, bütün workflow'ların harcaması sayılır.
        plan = budget.decide(data.config, now, evolve.FORMAT_NAMES, used_total=_month_total(now))
        result = evolve.generate(data.videos, data.dna, random.random, now, formats=plan['formats'],
                                 size=(plan['width'], plan['height']))
        payload = result['release_payload']
        meta = payload['metadata']
        meta['budget_level'] = plan['level']
        print(f"Yeni tarif: {payload['video_id']} | {meta['selection_mode']}/{meta['evolution']} | "
              f"{meta['format']} | {payload['structure']} | {payload['palette']} | ses: {meta['sound']}")
        if plan['used_total'] is None:
            spent = f"bu ay render {plan['used']:.0f}/{plan['budget']} dk"
        else:
            spent = (f"bu ay toplam {plan['used_total']:.0f}/{budget.MONTH_MINUTES} dk (render {plan['used']:.0f}), "
                     f"render'a kalan {plan['remaining']:.0f} dk, tur başına {plan['allowance']:.0f} dk")
        print(f"Bütçe: seviye {plan['level']}, {spent}, izinli: {', '.join(plan['formats'])}, "
              f"{payload['width']}x{payload['height']}")
    with open(os.path.join(out, 'release.json'), 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False)
    env_file = os.environ.get('GITHUB_ENV')
    if env_file:
        with open(env_file, 'a', encoding='utf-8') as f:
            f.write(f"RENDER_ID={payload['video_id']}\n")
    return 0


def _usage(args):
    """Render çalışmasının süresini aylık bütçeye yazar (kurulum dahil yaklaşık)."""
    try:
        minutes = (time.time() - float(args.since)) / 60.0 + 1.0
    except (TypeError, ValueError):
        raise OpsError('--since geçerli bir zaman damgası olmalı')
    fmt = None
    if args.spec and os.path.exists(args.spec):
        with open(args.spec, encoding='utf-8') as f:
            fmt = (json.load(f).get('metadata') or {}).get('format')
    now = datetime.now(timezone.utc)
    total = _month_total(now)                       # bu çalışma dahil, bütün workflow'lar
    month = {}

    def save(data):
        month.update(budget.record(data.config, now, minutes, fmt, used_total=total))
    Store.from_env().transaction(save, 'Actions dakika kullanımı')
    print(f"Bu çalışma yaklaşık {minutes:.1f} dk. Bu ay render toplam {month['render_min']:.0f} dk"
          + (f", bütün workflow'lar {total:.0f}/{budget.MONTH_MINUTES} dk." if total is not None else '.'))
    return 0


def _publish(args):
    publish.run(Store.from_env(), platform=args.platform, dry_run=args.dry_run)
    return 0


def _tiktok_connect(args):
    code = str(os.environ.get(args.code_env) or '').strip()
    if not code:
        raise OpsError(f'{args.code_env} ortam değişkeni boş: siteden gelen yetkilendirme kodu yok')
    tiktok.connect(Store.from_env(), code, datetime.now(timezone.utc), replace=args.replace)
    return 0


def _tiktok_stats(args):
    store = Store.from_env()
    token = tiktok.access_token(store, datetime.now(timezone.utc))
    videos = tiktok.list_videos(token, args.limit)
    print(f'TikTok: hesabın {len(videos)} videosu okundu.')
    for v in videos:
        created = datetime.fromtimestamp(int(v.get('create_time') or 0), timezone.utc).strftime('%Y-%m-%d %H:%M')
        print(f"{created} | {v.get('id')} | izlenme {v.get('view_count', 0)} | beğeni {v.get('like_count', 0)} | "
              f"yorum {v.get('comment_count', 0)} | paylaşım {v.get('share_count', 0)} | {str(v.get('title') or '')[:50]}")
    return 0


def _tiktok_sync(args):
    """Paylaşılan taslakları bulur, ölçer, skorlar. `--refresh` yoksa jeton yenilenmez (öğrenme turu böyle çağırır):
    TikTok kurulu değilse, bağlı değilse ya da jetonun süresi dolmuşsa tur hata vermeden atlanır."""
    store = Store.from_env()
    now = datetime.now(timezone.utc)
    if args.refresh:
        token = tiktok.access_token(store, now)
    else:
        try:
            token = tiktok.stored_access_token(store.read(), now)
        except (CredentialsMissing, TiktokNotConnected) as e:
            print(f'TikTok atlandı: {e}')
            return 0
        if token is None:
            print('TikTok atlandı: erişim jetonunun süresi dolmuş. Sonraki yükleme turu yeniler, ölçümler o zaman alınır.')
            return 0
    try:
        tiktok_sync.run(store, token, now, dry_run=args.dry_run)
    except net.HttpError as e:
        if e.status == 401 and not args.refresh:        # jeton bu sırada yenilenmiş olabilir: sonraki tur yenisini okur
            print(f'TikTok atlandı: erişim jetonu kabul edilmedi ({e}). Sonraki turda yeniden denenir.')
            return 0
        raise OpsError(f'TikTok okunamadı: {e}',
                       hint="TikTok'tan video listesi ya da sayılar alınamadı, kayıt değişmedi. Geçiciyse sonraki tur "
                            "(üç saatte bir) kendiliğinden dener. Sürerse siteden yeniden bağlanın (README.md, TikTok / dashboard bölümü).")
    return 0


def _learn(args):
    learn.run(Store.from_env())
    return 0


def _token(args):
    res = tokens.refresh(Store.from_env())
    print(f"Jeton yenilendi. Yeni son kullanma: {res['expires_at']}")
    return 0


def _report(args):
    data = Store.from_env().read()
    now = datetime.now(timezone.utc)
    built = report.build_report(data.videos, data.dna, now.timestamp() * 1000, usage=budget.summary(data.config, now))
    issue = alerts.post_report(built['subject'], built['message'])
    print(f"Haftalık özet gönderildi: {issue.get('html_url', '')}")
    return 0


def _ping(args):
    issue = alerts.ping()
    print(f"Bildirim denemesi gönderildi: {issue.get('html_url', '')}")
    return 0


def _alert(args):
    print('Bildirim:', alerts.report_failure(args.workflow, args.run_url or ''))
    return 0


def _resolve(args):
    n = alerts.resolve(args.workflow)
    print(f'Kapatılan hata kaydı: {n}')
    return 0


def _watch(args):
    watch.run(after_render=args.after_render)
    return 0


def _slot(args):
    """Zamanlı yedek tur: saat bu yuvayı çoktan başlattıysa iş yapma."""
    import subprocess
    fired = os.environ.get('FORMFORM_SCHEDULE') or ''
    now = datetime.now(timezone.utc)
    slot = schedule.latest_occurrence(fired, now)
    skip = False
    if slot is not None:
        proc = subprocess.run(
            ['gh', 'run', 'list', '--workflow', f'{args.job}.yml', '--limit', '20',
             '--json', 'displayTitle,createdAt,conclusion,status'],
            capture_output=True, text=True)
        if proc.returncode == 0:
            try:
                runs = json.loads(proc.stdout or '[]')
            except json.JSONDecodeError:
                runs = []
            skip = schedule.clock_run_covers(runs, slot)
    line = f"skip={'true' if skip else 'false'}\n"
    out = os.environ.get('GITHUB_OUTPUT')
    if out:
        with open(out, 'a', encoding='utf-8') as f:
            f.write(line)
    print('Saat bu yuvayı aldı, yedek tur iş yapmayacak.' if skip else 'Bu yuva için saat turu yok.')
    return 0


def _season(args):
    """Zamanlı tur bu mevsimin cron’u mu. Sonucu GITHUB_OUTPUT’a yazar."""
    event = os.environ.get('FORMFORM_EVENT') or 'workflow_dispatch'
    fired = os.environ.get('FORMFORM_SCHEDULE') or ''
    run = schedule.should_run(args.job, event, fired, datetime.now(timezone.utc))
    line = f"run={'true' if run else 'false'}\n"
    out = os.environ.get('GITHUB_OUTPUT')
    if out:
        with open(out, 'a', encoding='utf-8') as f:
            f.write(line)
    print('Bu tur Berlin saatinde doğru.' if run else 'Bu tur diğer mevsimin zamanı, iş yapılmayacak.')
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog='ops')
    sub = p.add_subparsers(dest='command', required=True)
    s = sub.add_parser('plan', help='evrim motoru: yeni render tarifi')
    s.add_argument('--out', required=True)
    s.add_argument('--spec-env', help='dolu ise tarif bu ortam değişkeninden alınır (elle deneme)')
    s.set_defaults(fn=_plan)
    s = sub.add_parser('usage', help='render dakikasını aylık bütçeye yaz')
    s.add_argument('--since', required=True, help='işin başlangıç zamanı (epoch saniye)')
    s.add_argument('--spec', help='render tarifi (release.json): format bilgisi için')
    s.set_defaults(fn=_usage)
    s = sub.add_parser('publish', help='en yeni hazır render\'ı bir platforma paylaş (varsayılan: Instagram)')
    s.add_argument('--platform', default=platforms.DEFAULT, choices=tuple(platforms.PLATFORMS),
                   help='hangi platform (şimdilik yalnız Instagram kurulu)')
    s.add_argument('--dry-run', action='store_true', help='paylaşmadan her şeyi sına')
    s.set_defaults(fn=_publish)
    s = sub.add_parser('tiktok-connect', help='siteden gelen TikTok yetkilendirme kodunu jetona çevirip kaydet')
    s.add_argument('--code-env', default='TIKTOK_CODE', help='kodun okunacağı ortam değişkeni (komut satırına yazılmaz)')
    s.add_argument('--replace', action='store_true', help='bağlı olandan farklı bir TikTok hesabının yerine geçmesine izin ver')
    s.set_defaults(fn=_tiktok_connect)
    s = sub.add_parser('tiktok-stats', help='bağlı TikTok hesabının kendi videolarının sayıları (yalnız okuma)')
    s.add_argument('--limit', type=int, default=20)
    s.set_defaults(fn=_tiktok_stats)
    s = sub.add_parser('tiktok-sync', help='paylaşılan TikTok taslaklarını bul, 24/72 saatlik sayıları oku, skorla')
    s.add_argument('--refresh', action='store_true',
                   help='erişim jetonu bitmek üzereyse yenile (yalnız TikTok workflow\'unda: yenilemeler yarışmasın)')
    s.add_argument('--dry-run', action='store_true', help='kayda yazmadan ne bulunduğunu göster')
    s.set_defaults(fn=_tiktok_sync)
    sub.add_parser('learn', help='ölçümler, skorlar, ağırlıklar').set_defaults(fn=_learn)
    sub.add_parser('token', help='Instagram jetonunu yenile').set_defaults(fn=_token)
    sub.add_parser('report', help='haftalık özet').set_defaults(fn=_report)
    sub.add_parser('ping', help='bildirim denemesi (issue açar, e-posta gelmeli)').set_defaults(fn=_ping)
    s = sub.add_parser('alert', help='hata bildirimi')
    s.add_argument('--workflow', required=True)
    s.add_argument('--run-url')
    s.set_defaults(fn=_alert)
    s = sub.add_parser('resolve', help='açık hata kaydını kapat')
    s.add_argument('--workflow', required=True)
    s.set_defaults(fn=_resolve)
    s = sub.add_parser('watch', help='nöbetçi: saatin kaçırdığı render, yayın, TikTok ve öğrenme turlarını başlat')
    s.add_argument('--after-render', action='store_true',
                   help='render turunun sonunda: yalnız kaçmış yayın ve TikTok yuvalarını başlat, hata verme')
    s.set_defaults(fn=_watch)
    s = sub.add_parser('slot', help='saat bu yuvayı başladıysa zamanlı yedek iş yapmasın')
    s.add_argument('--job', required=True, choices=tuple(schedule.JOBS))
    s.set_defaults(fn=_slot)
    s = sub.add_parser('season', help='zamanlı tur Berlin’de bu mevsimin cron’u mu')
    s.add_argument('--job', required=True, choices=tuple(schedule.JOBS))
    s.set_defaults(fn=_season)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except OpsError as e:
        message, hint = str(e), e.hint
    except Exception as e:                                # beklenmeyen hata
        message, hint = f'{type(e).__name__}: {e}', OpsError.hint
    message = net.scrub(message)
    print(f'::error::{message}')
    if args.command not in ('alert', 'resolve', 'ping'):
        alerts.remember_error(args.command, message, hint)
    return 1


if __name__ == '__main__':
    sys.exit(main())
