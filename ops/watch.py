"""NÖBETÇİ: saat (Cloudflare Worker) bir turu kaçırdıysa onu başlatır.

Render, yayın, TikTok ve öğrenme turlarını saat başlatır. Eskiden her birinin GitHub'da iki yedek cron'u vardı:
saat işi yaptığında bile günde 20'ye yakın boş tur açılıyor, her biri 1 dakika sayılıyordu. Onların yerini günde iki
kez çalışan bu tek tur aldı (`watch.yml`). Her şey yolundaysa birkaç saniyede biter.

Bir yuva "kaçmış" sayılır: üstünden en az `WATCH_GRACE_MIN` dakika geçmiş (saatin kendi 20 dakikalık penceresi
kapanmış), en çok `WATCH_MAX_AGE_H` saatlik ve o yuvadan beri başarısız olmamış hiçbir çalışma açılmamışsa. Nöbetçi
saate değil duruma bakar: GitHub onu saatlerce geciktirse de aynı kararı verir, iki kez çalışsa da çift tur açmaz
(başlattığı tur yuvayı doldurur).

Sıra: render kaçmışsa yalnız render başlatılır. Yayın ve TikTok hazır video ister, onları render bitince render'ın
kendisi başlatır (`render.yml` → `ops watch --after-render`). Böylece saat tamamen dursa bile videolar günde iki kez,
nöbetçinin saatlerinde çıkmaya devam eder.

Hiç başlamamış bir tur bulunursa çalışma hata ile biter (bildirim gider): saat çalışmıyor olabilir. Yalnız başarısız
olmuş turlar yeniden başlatıldıysa hata verilmez, o workflow kendi hata bildirimini zaten açmıştır.
"""
from datetime import datetime, timedelta, timezone

from . import github, net, schedule
from .errors import ClockMissed, OpsError

FILES = {'render': 'render.yml', 'publish': 'publish.yml', 'tiktok': 'tiktok.yml', 'learn': 'learn.yml'}
AFTER_RENDER = ('publish', 'tiktok')          # hazır render ister
NEVER, FAILED = 'hiç başlamadı', 'başarısız oldu'


def missed(now, jobs, list_runs, max_age_hours=schedule.WATCH_MAX_AGE_H):
    """Kaçan yuvalar: [(iş, yuva, durum, başlatılır mı)]. Durum: hiç başlamadı ya da başarısız oldu.

    Çok eski yuva başlatılmaz (sıradaki yuva onu zaten yapacak) ama yine bildirilir. Yayın ve TikTok'un yaşı kendi
    yuvasından değil o turun render yuvasından sayılır: render'ı artık başlatılmayacak bir turun yayını da başlatılmaz
    (hazır video olmaz). max_age_hours=None: yaşa bakılmaz (render turunun kendi sonunda)."""
    out = []
    for job in jobs:
        slot = schedule.latest_slot(job, now)
        if slot is None:
            continue
        runs = list_runs(FILES[job])
        if schedule.covered(runs, slot):
            continue
        cycle = schedule.latest_slot('render', slot, grace_minutes=0) if job in AFTER_RENDER else slot
        fresh = max_age_hours is None or now - (cycle or slot) <= timedelta(hours=max_age_hours)
        out.append((job, slot, FAILED if schedule.started_since(runs, slot) else NEVER, fresh))
    return out


def _label(job, slot, state):
    return f"{job} {slot.astimezone(schedule.TZ).strftime('%H:%M')} ({state})"


def run(now=None, after_render=False, log=print):
    """Kaçan turları başlatır. after_render: render turunun sonunda yalnız yayın ve TikTok'a bakar, hata vermez."""
    now = now or datetime.now(timezone.utc)
    try:
        found = missed(now, AFTER_RENDER if after_render else schedule.WATCHED, github.list_workflow_runs,
                       max_age_hours=None if after_render else schedule.WATCH_MAX_AGE_H)
    except net.HttpError as e:
        raise OpsError(f'Nöbetçi çalışmaları okuyamadı: {e}',
                       hint='GitHub çalışma listesi alınamadı, hiçbir tur başlatılmadı. Geçiciyse sonraki nöbet dener.')
    if not found:
        log('Nöbet: bütün turlar zamanında başlamış.')
        return {'started': [], 'waiting': [], 'stale': [], 'never': []}

    render_starts = any(job == 'render' and fresh for job, _, _, fresh in found)
    started, waiting, stale, never = [], [], [], []
    for job, slot, state, fresh in found:
        label = _label(job, slot, state)
        if state == NEVER:
            never.append(label)
        if not fresh:
            stale.append(label)
            log(f'Başlatılmadı: {label}. Yuva {schedule.WATCH_MAX_AGE_H} saatten eski, sıradaki yuva yapacak.')
            continue
        if render_starts and job in AFTER_RENDER:
            waiting.append(label)
            log(f'Bekliyor: {label}. Hazır video yok, render bitince render turu başlatacak.')
            continue
        try:
            github.dispatch_workflow(FILES[job], {'watch': 'true'})
        except net.HttpError as e:
            raise OpsError(f'Nöbetçi {label} turunu başlatamadı: {e}',
                           hint="Workflow başlatılamadı. Actions sekmesinden o workflow'u elle çalıştırın (Run workflow).")
        started.append(label)
        log(f'Başlatıldı: {label}')

    if never and not after_render:
        did = []
        if started:
            did.append('Nöbetçi başlattı: ' + ', '.join(started) + '.')
        if waiting:
            did.append('Render bitince başlayacak: ' + ', '.join(waiting) + '.')
        if stale:
            did.append('Çok eski olduğu için başlatılmadı: ' + ', '.join(stale) + '.')
        raise ClockMissed('Saat şu turları başlatmadı: ' + ', '.join(never) + '. ' + ' '.join(did))
    return {'started': started, 'waiting': waiting, 'stale': stale, 'never': never}
