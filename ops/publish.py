"""YAYIN: en yeni hazır render'ı bir platforma paylaşır. Bugün yalnız Instagram (Reel) kurulu.

Her platformun yayını ayrı çalışır ve kendi durumunu tutar (`ops/platforms.py`, `ops/ledger.py`). Biri hata verince
ötekiler etkilenmez. Instagram'ın durumu release adındadır, diğerlerinin durumu kendi kaydındadır.

Eski n8n PUBLISH workflow'unun karşılığı. Instagram'da sıra ve güvenceler aynıdır:
  1. jeton yoksa hiçbir şey yapmadan hata ver
  2. GitHub release'lerinden en yeni hazır render'ı seç ('render <id>')
  3. ÜSTLEN: release adını 'claimed <id>' yap (çift paylaşımı önler, atlanamaz)
  4. indir → Cloudinary → Instagram Reel (ERROR/EXPIRED olursa dur)
  5. videos.csv'ye satır yaz, release adını 'published <id>' yap

Başarısız yayında release 'claimed' kalır ve otomatik denenmez. Üstlenme, Reel oluşturma ve paylaşma
çağrıları asla yeniden denenmez.
"""
import json
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone

from . import alerts, cloudinary, config, github, instagram, ledger, net, platforms, tiktok, tokens, youtube
from .errors import (NotReady, OpsError, PublishUncertain, ReelFailed, StoreError, TiktokFailed, TiktokUncertain,
                     YoutubeFailed, YoutubeUncertain)
from .jsnum import num_or0, parse_time_ms


def _created_ms(release):
    """Render'ın gerçekten yayınlandığı an.

    GitHub'ın `created_at` alanı release'in oluşturulma anı DEĞİLDİR: etiketin gösterdiği commit'in tarihidir
    (aynı commit'ten çıkan release'lerin hepsinde aynı, main'e uzun süre commit gelmezse hepsi "eski"). Gerçek an
    `published_at`'tır. Canlıda yakalandı: sabah çizilen render'lar 25 saatlik görünüyordu. `published_at` yoksa
    (eski kayıt, sahte sunucu) `created_at`'a düşülür."""
    t = parse_time_ms(release.get('published_at') or release.get('created_at'))
    return 0 if t != t else t


STATES = ('render ', 'claimed ', 'published ')


def pick_ready(releases, taken=None, now_ms=None, max_age_hours=None):
    """En yeni geçerli ve hazır render. Hazır: video.mp4 yüklenmiş release.

    Instagram (`taken` yok): adı 'render ' ile başlayan release. Yayınlanan 'published ...', üstlenilen
    'claimed ...' adını taşır, bunlar bir daha seçilmez. Release adındaki durum Instagram'ındır.

    Diğer platformlar (`taken` verilir): release adındaki durum yok sayılır (o Instagram'ın durumu). Platformun
    kaydında zaten olan video kimlikleri (`taken`) atlanır. `now_ms` ve `max_age_hours` verilirse, o kadar saatten
    eski render'lar da atlanır: kaçırılan yuvada günler önceki bir video paylaşılmasın.

    Gövdesi bozuk ya da eksik (ör. elle denenmiş render) olan release atlanır, sıradakine bakılır."""
    def has_video(r):
        return any(a.get('name') == 'video.mp4' and a.get('state') == 'uploaded' for a in (r.get('assets') or []))

    prefixes = ('render ',) if taken is None else STATES
    ready = [r for r in releases or [] if r and r.get('id') and str(r.get('name') or '').startswith(prefixes)
             and not r.get('draft') and has_video(r)]
    ready.sort(key=lambda r: -_created_ms(r))
    if not ready:
        return {'ready': False, 'reason': 'hazır render yok (render.yml çalışmadı, bitmedi ya da başarısız oldu)'}

    skipped, done, stale = [], 0, 0
    for release in ready:
        try:
            payload = json.loads(release.get('body') or '')
        except ValueError:
            skipped.append(f"{release['name']}: gövde JSON değil")
            continue
        if not isinstance(payload, dict) or not payload.get('video_id') or not payload.get('caption') \
                or not payload.get('metadata'):
            skipped.append(f"{release['name']}: video_id, caption ya da metadata eksik")
            continue
        if taken is not None and payload['video_id'] in taken:
            done += 1
            continue
        if max_age_hours is not None and now_ms is not None \
                and (now_ms - _created_ms(release)) > max_age_hours * 3600 * 1000:
            stale += 1
            continue
        asset = next(a for a in release['assets'] if a.get('name') == 'video.mp4' and a.get('state') == 'uploaded')
        return {'ready': True, 'release_id': release['id'], 'tag': release.get('tag_name'),
                'asset_url': asset['url'], 'payload': payload}
    why = list(skipped)
    if done:
        why.append(f'{done} render bu platformda zaten üstlenilmiş ya da yayınlanmış')
    if stale:
        why.append(f'{stale} render {max_age_hours} saatten eski')
    return {'ready': False, 'reason': f"hazır render var ama yayınlanamaz: {'; '.join(why)}"}


def loop_url_for(payload, secure_url):
    """seamless: video zaten kusursuz döner. forward: Cloudinary boomerang ile döngüye çevrilir."""
    if payload.get('loop') == 'forward':
        return secure_url.replace('/video/upload/', '/video/upload/e_boomerang/')
    return secure_url


def recipe_row(payload, now):
    """Videonun tarifi (genleri) ve boş ölçüm alanları: her platformun kaydı bu satırla başlar."""
    meta = payload.get('metadata') or {}

    def join_list(v):
        return ', '.join(v) if isinstance(v, list) else (v or '')

    def weight(v):
        n = num_or0(v)
        return n if n else 1

    return {
        'video_id': payload.get('video_id'),
        'created_at': meta.get('created_at') or '',
        'structure': meta.get('structure') or '',
        'palette': meta.get('palette') or '',
        'materials': join_list(meta.get('materials')),
        'transformations': join_list(meta.get('transformations')),
        'motion_quality': meta.get('motion_quality') or '',
        'lighting': meta.get('lighting') or '',
        'loop_strategy': meta.get('loop_strategy') or '',
        'caption': payload.get('caption') or '',
        'published_at': ledger.stamp(now),
        'views_24h': '', 'views_72h': '', 'likes': '', 'comments': '', 'shares': '', 'saves': '',
        'quality_score': '', 'performance_score': '',
        'selection_mode': meta.get('selection_mode') or 'random',
        'structure_weight': weight(meta.get('structure_weight')),
        'palette_weight': weight(meta.get('palette_weight')),
        'motion_weight': weight(meta.get('motion_weight')),
        'lighting_weight': weight(meta.get('lighting_weight')),
        'avg_watch_time': '',
        'sound': meta.get('sound') or '',
    }


def build_log_row(payload, instagram_id, now):
    return dict(recipe_row(payload, now), instagram_id=instagram_id)


def wait_for_reel(token, creation_id, sleep=time.sleep, poll=None, max_wait=None):
    """Instagram videoyu işleyene kadar bekler. FINISHED → devam, ERROR/EXPIRED → durur."""
    poll = config.REEL_POLL_SECONDS if poll is None else poll
    max_wait = config.REEL_MAX_WAIT_SECONDS if max_wait is None else max_wait
    waited, last = 0, {}
    while True:
        sleep(poll)
        waited += poll
        last = instagram.reel_status(token, creation_id)
        code = last.get('status_code') if isinstance(last, dict) else None
        if code == 'FINISHED':
            return
        if code in ('ERROR', 'EXPIRED'):
            raise ReelFailed(f"Instagram video işlenemedi: {code} {last.get('status') or ''}".strip())
        if waited >= max_wait:
            raise ReelFailed(f'Instagram videoyu {max_wait // 60} dakikada işleyemedi (son durum: {code}).')


def run_instagram(store, dry_run=False, clock=None, sleep=time.sleep, workdir=None, log=print):
    clock = clock or (lambda: datetime.now(timezone.utc))
    data = store.read()
    token = tokens.current(data)                       # jeton yoksa burada, hiçbir şey yapmadan durur
    if dry_run:
        who = instagram.me(token)
        log(f"Jeton geçerli (hesap: {who.get('username', '?') if isinstance(who, dict) else '?'}).")

    pick = pick_ready(github.list_releases())
    if not pick['ready']:
        raise NotReady('Yayınlanacak render yok: ' + pick['reason'])
    payload = pick['payload']
    video_id = payload['video_id']
    log(f'Seçilen render: {video_id} (release {pick["release_id"]})')

    workdir = workdir or tempfile.mkdtemp(prefix='ff-publish-')
    path = os.path.join(workdir, 'video.mp4')

    if dry_run:
        log('DENEME: üstlenme, paylaşım ve kayıt YAPILMAYACAK. Video indirilip Cloudinary yüklemesi sınanacak.')
        github.download_asset(pick['asset_url'], path)
        up = cloudinary.upload_video(path, f'{video_id}_deneme')
        cloudinary.destroy(f'{video_id}_deneme')
        log(f"Cloudinary yükleme tamam ({os.path.getsize(path) // 1024} KB). Her şey hazır.")
        return {'dry_run': True, 'video_id': video_id, 'url': up['secure_url']}

    # Üstlen: bundan sonra hata olursa release 'claimed' kalır ve otomatik denenmez
    github.rename_release(pick['release_id'], f'claimed {video_id}')
    log(f'Üstlenildi: claimed {video_id}')

    github.download_asset(pick['asset_url'], path)
    up = cloudinary.upload_video(path, video_id)
    video_url = loop_url_for(payload, up['secure_url'])
    log('Video Cloudinary\'ye yüklendi.')

    try:
        creation_id = instagram.create_reel(token, video_url, payload['caption'])
    except net.HttpError as e:
        raise ReelFailed(f'Instagram reel oluşturamadı: {e}',
                         hint='Jeton ya da video adresi sorunlu olabilir. Release "claimed" kaldı, otomatik denenmez. '
                              'Sebebi giderince Releases sayfasında adını "render <video_id>" yapıp yeniden çalıştırın.')
    wait_for_reel(token, creation_id, sleep=sleep)
    try:
        media_id = instagram.publish_reel(token, creation_id)
    except net.HttpError as e:
        raise PublishUncertain(f'Instagram paylaşımı yanıtsız ya da hatalı ({creation_id}): {e}')
    log(f'Paylaşıldı: Instagram id {media_id}')

    # Paylaşım yapıldı. Bundan sonraki hatalar paylaşımı geri almaz, yalnız bildirilir.
    problem = None
    row = build_log_row(payload, media_id, clock())

    def append(d):
        if not any(r.get('video_id') == row['video_id'] for r in d.videos):
            d.videos.append(row)

    try:
        store.transaction(append, f'Yayınlandı: {video_id}')
    except OpsError as e:
        problem = StoreError(f'Video paylaşıldı (Instagram id: {media_id}, video: {video_id}) ama kayıt yazılamadı: {e}',
                             hint='Paylaşım tamam, tekrar paylaşmayın. Kaydı elle eklemek için veri dalındaki '
                                  'videos.csv dosyasına satır ekleyin ya da bu videoyu öğrenmeden çıkarın.')
    try:
        github.rename_release(pick['release_id'], f'published {video_id}')
    except net.HttpError as e:
        log(f'Uyarı: release adı "published" yapılamadı: {e}')
    cloudinary.destroy(video_id)
    if problem:
        raise problem
    return {'video_id': video_id, 'instagram_id': media_id}


def run_youtube(store, dry_run=False, clock=None, workdir=None, log=print):
    """YouTube Shorts yayını. Instagram'dan bağımsızdır: kendi durumu `youtube/videos.csv`'dedir, release adına dokunmaz.

    Sıra: kimlik yoksa hiçbir şey yapmadan dur → erişim jetonu → hazır render'ı seç (bu kanalda üstlenilmemiş) →
    ÜSTLEN (kayda yaz, atlanamaz) → indir → yükleme oturumu aç → dosyayı gönder → kaydı 'published' yap.
    Üstlenmeden sonraki hata satırı 'claimed' bırakır ve otomatik denenmez. Oturum açma ve dosya gönderme yeniden denenmez."""
    clock = clock or (lambda: datetime.now(timezone.utc))
    privacy = youtube.privacy()
    token = youtube.access_token()                    # kimlik yoksa ya da geçersizse burada, hiçbir şey yapmadan durur
    now = clock()
    data = store.read()
    pick = pick_ready(github.list_releases(), taken=ledger.taken_ids(data, 'youtube'),
                      now_ms=now.timestamp() * 1000, max_age_hours=config.PLATFORM_MAX_AGE_HOURS)
    if not pick['ready']:
        raise NotReady('Yayınlanacak render yok (YouTube): ' + pick['reason'])
    payload = pick['payload']
    video_id = payload['video_id']
    log(f'Seçilen render: {video_id} (release {pick["release_id"]})')

    workdir = workdir or tempfile.mkdtemp(prefix='ff-publish-yt-')
    path = os.path.join(workdir, 'video.mp4')

    if dry_run:
        log(f'Kimlik geçerli (kanal: {youtube.channel(token)}).')
        log('DENEME: üstlenme, yükleme ve kayıt YAPILMAYACAK. Video indirilecek.')
        github.download_asset(pick['asset_url'], path)
        log(f'İndirme tamam ({os.path.getsize(path) // 1024} KB). Her şey hazır.')
        return {'dry_run': True, 'video_id': video_id}

    # Üstlen: bundan sonra hata olursa satır 'claimed' kalır ve otomatik denenmez
    if not ledger.claim(store, 'youtube', recipe_row(payload, now)):
        raise NotReady(f'{video_id} YouTube için zaten üstlenilmiş (başka bir çalışma ya da yarım kalmış yayın).',
                       hint="Veri dalındaki youtube/videos.csv'de o satırı kontrol edin. Yarım kalmışsa ve YouTube'da "
                            "video yoksa o satırı silin, böylece yeniden denenir.")
    log(f'Üstlenildi (YouTube): {video_id}')

    github.download_asset(pick['asset_url'], path)
    try:
        session = youtube.start_upload(token, os.path.getsize(path), youtube.metadata(payload['caption'], privacy))
    except net.HttpError as e:
        raise YoutubeFailed(f'YouTube yüklemeyi başlatamadı: {e}')
    try:
        youtube_id = youtube.send_video(token, session, path)
    except net.HttpError as e:
        raise YoutubeUncertain(f'YouTube yüklemesi yanıtsız ya da hatalı ({video_id}): {e}')
    log(f'Yüklendi: YouTube id {youtube_id}')

    # Yükleme yapıldı. Bundan sonraki hata paylaşımı geri almaz, yalnız bildirilir.
    try:
        ledger.settle(store, 'youtube', video_id, youtube_id, clock())
    except OpsError as e:
        raise StoreError(f'Video yüklendi (YouTube id: {youtube_id}, video: {video_id}) ama kayıt yazılamadı: {e}',
                         hint="Yükleme tamam, tekrar paylaşmayın. Kaydı elle tamamlamak için veri dalındaki "
                              "youtube/videos.csv'de o satıra video kimliğini yazıp status'u 'published' yapın.")
    return {'video_id': video_id, 'youtube_id': youtube_id}


def wait_for_tiktok(token, publish_id, sleep=time.sleep, poll=None, max_wait=None):
    """TikTok videoyu işleyip gelen kutusuna taslak olarak bırakana kadar bekler. FAILED olursa durur."""
    poll = config.TIKTOK_POLL_SECONDS if poll is None else poll
    max_wait = config.TIKTOK_MAX_WAIT_SECONDS if max_wait is None else max_wait
    waited = 0
    while True:
        sleep(poll)
        waited += poll
        try:
            last = tiktok.publish_status(token, publish_id)
        except net.HttpError as e:
            raise TiktokUncertain(f'TikTok yükleme durumu alınamadı ({publish_id}): {e}')
        status = last.get('status')
        if status in ('SEND_TO_USER_INBOX', 'PUBLISH_COMPLETE'):
            return status
        if status == 'FAILED':
            raise TiktokFailed(f"TikTok videoyu işleyemedi: {last.get('fail_reason') or 'bilinmeyen sebep'}")
        if waited >= max_wait:
            raise TiktokUncertain(f'TikTok videoyu {max_wait // 60} dakikada işlemedi (son durum: {status}).')


def _step_summary(text):
    """Actions çalışma özetine yazar (telefondaki GitHub uygulamasından başlık kolayca kopyalanır)."""
    path = os.environ.get('GITHUB_STEP_SUMMARY')
    if path:
        with open(path, 'a', encoding='utf-8') as f:
            f.write(text + '\n')


class NothingUploaded(Exception):
    """Yükleme denemelerinin hepsi "hiçbir şey oluşmadı" kanıtıyla başarısız oldu (taslak yok)."""


def _tiktok_send(token, path, size, video_id, sleep, log):
    """Yükleme oturumunu açıp dosyayı gönderir, publish_id döndürür.

    Yalnız hiçbir şeyin oluşmadığı KANITLI durumlarda yeni bir oturumla tekrar dener: başlatma çağrısı hatası (henüz
    dosya gitmedi) ve yükleme adresinde 404 (oturum yok, dosya ulaşmadı). Dosya gönderiminin diğer hataları
    (zaman aşımı, 5xx, bağlantı kopması) belirsizdir: taslak oluşmuş olabilir, ASLA yeniden denenmez."""
    waits = config.TIKTOK_RETRY_WAITS
    last = ''
    for attempt in range(config.TIKTOK_UPLOAD_ATTEMPTS):
        if attempt:
            wait = waits[min(attempt - 1, len(waits) - 1)]
            log(f'TikTok yüklemesi {wait} sn sonra yeni oturumla yeniden denenecek '
                f'({attempt + 1}/{config.TIKTOK_UPLOAD_ATTEMPTS}). Önceki hata: {last}')
            sleep(wait)
        try:
            publish_id, upload_url = tiktok.init_upload(token, size)
        except net.HttpError as e:
            last = f'yükleme başlatılamadı: {e}'
            continue
        try:
            tiktok.send_video(upload_url, path)
            return publish_id
        except net.HttpError as e:
            if e.status == 404:
                last = f'dosya ulaşmadı (yükleme adresi 404): {e}'
                continue
            raise TiktokUncertain(f'TikTok yüklemesi yanıtsız ya da hatalı ({video_id}): {e}')
    raise NothingUploaded(last)


def run_tiktok(store, dry_run=False, clock=None, sleep=time.sleep, workdir=None, log=print):
    """TikTok TASLAK yayını: video kullanıcının TikTok gelen kutusuna taslak olarak yüklenir, paylaşımı kullanıcı yapar.

    TikTok'un otomatik paylaşım (Direct Post) kuralı her paylaşım için kullanıcının açık onayını ister, bu yüzden
    kullanılmaz. Güvenceler diğer platformlarla aynıdır: üstlenme yüklemeden önce ve atlanamaz, yükleme başlatma ve
    dosya gönderme yeniden denenmez, hatada satır 'claimed' kalır. Başarıda satır 'drafted' olur (yayın zamanı boş:
    paylaşımı kullanıcı yaptığında belli olur, satır o zamana kadar öğrenmeye girmez)."""
    clock = clock or (lambda: datetime.now(timezone.utc))
    now = clock()
    # Bağlı değilse ya da jeton ölmüşse burada, hiçbir şey yapmadan durur. Jeton her yükleme turunda yenilenir.
    token = tiktok.access_token(store, now, margin=timedelta(hours=config.TIKTOK_UPLOAD_REFRESH_HOURS))
    data = store.read()
    pick = pick_ready(github.list_releases(), taken=ledger.taken_ids(data, 'tiktok'),
                      now_ms=now.timestamp() * 1000, max_age_hours=config.PLATFORM_MAX_AGE_HOURS)
    if not pick['ready']:
        raise NotReady('Yayınlanacak render yok (TikTok): ' + pick['reason'])
    payload = pick['payload']
    video_id = payload['video_id']
    log(f'Seçilen render: {video_id} (release {pick["release_id"]})')

    workdir = workdir or tempfile.mkdtemp(prefix='ff-publish-tt-')
    path = os.path.join(workdir, 'video.mp4')

    if dry_run:
        log(f"Kimlik geçerli (hesap: {tiktok.user_info(token).get('display_name') or '?'}).")
        log('DENEME: üstlenme, yükleme ve kayıt YAPILMAYACAK. Video indirilecek.')
        github.download_asset(pick['asset_url'], path)
        log(f'İndirme tamam ({os.path.getsize(path) // 1024} KB). Her şey hazır.')
        return {'dry_run': True, 'video_id': video_id}

    # Önce indir ve boyutu denetle (yan etkisiz): değişmez bir hata satırı 'claimed' bırakıp elle temizlik gerektirmesin
    github.download_asset(pick['asset_url'], path)
    size = os.path.getsize(path)
    if size > config.TIKTOK_MAX_SINGLE_CHUNK:
        raise OpsError(f'Video {size // (1024 * 1024)} MB: tek parça yükleme sınırını '
                       f'({config.TIKTOK_MAX_SINGLE_CHUNK // (1024 * 1024)} MB) aşıyor, parçalı yükleme henüz yazılmadı. '
                       'Hiçbir şey yüklenmedi.')

    # Üstlen: bundan sonra hata olursa satır 'claimed' kalır ve otomatik denenmez
    if not ledger.claim(store, 'tiktok', recipe_row(payload, now)):
        raise NotReady(f'{video_id} TikTok için zaten üstlenilmiş (başka bir çalışma ya da yarım kalmış yükleme).',
                       hint="Veri dalındaki tiktok/videos.csv'de o satırı kontrol edin. Yarım kalmışsa ve TikTok gelen "
                            "kutusunda taslak yoksa o satırı silin, böylece yeniden denenir.")
    log(f'Üstlenildi (TikTok): {video_id}')

    try:
        publish_id = _tiktok_send(token, path, size, video_id, sleep, log)
    except NothingUploaded as e:
        # Hiçbir şeyin yüklenmediği kanıtlı: üstlenmeyi serbest bırak ki sonraki tur (Worker saati 20 dk içinde ya da
        # nöbetçi) elle müdahale beklemeden yeniden denesin.
        released = False
        try:
            released = ledger.unclaim(store, 'tiktok', video_id)
        except OpsError as unclaim_error:
            log(f'Uyarı: üstlenme serbest bırakılamadı: {unclaim_error}')
        message = f'TikTok videoyu {config.TIKTOK_UPLOAD_ATTEMPTS} denemede de alamadı, hiçbir şey yüklenmedi ({e})'
        if released:
            raise TiktokFailed(message, hint="Üstlenme serbest bırakıldı, taslak oluşmadı. Sonraki tur kendiliğinden "
                                             "yeniden dener (Worker saati 20 dakika içinde, olmazsa nöbetçi). "
                                             "Elle denemek için FORMFORM tiktok → Run workflow → action upload.")
        raise TiktokFailed(message)
    status = wait_for_tiktok(token, publish_id, sleep=sleep)
    log(f'TikTok durumu: {status} (publish_id {publish_id})')

    # Taslak API'si başlık/hashtag kabul etmez: başlık sahibine kopyalanabilir bir bildirim olarak gider. En iyi çaba:
    # bildirim gidemezse yükleme bozulmaz (taslak zaten gelen kutusunda), başlık günlükte ve çalışma özetinde de var.
    try:
        alerts.post_note(f'[FORMFORM] TikTok taslağı hazır: {video_id}',
                         'TikTok → Gelen Kutusu → taslağı açın, aşağıdaki başlığı kopyalayıp yapıştırın ve paylaşın:',
                         payload['caption'])
    except (OpsError, net.HttpError) as e:
        log(f'Uyarı: başlık bildirimi gönderilemedi (taslak yüklendi): {e}')

    # Taslak gelen kutusunda. Bundan sonraki hata taslağı geri almaz, yalnız bildirilir.
    try:
        ledger.settle(store, 'tiktok', video_id, publish_id, now, status='drafted')
    except OpsError as e:
        raise StoreError(f'Taslak yüklendi (TikTok publish_id: {publish_id}, video: {video_id}) ama kayıt yazılamadı: {e}',
                         hint="Taslak tamam, tekrar yüklemeyin. Kaydı elle tamamlamak için veri dalındaki "
                              "tiktok/videos.csv'de o satıra publish_id'yi yazıp status'u 'drafted' yapın.")
    caption = payload['caption']
    log('TikTok uygulamasında Gelen Kutusu\'ndaki taslağı açıp paylaşın. Başlığı yapıştırın:')
    log(caption)
    _step_summary(f'### TikTok taslağı hazır: {video_id}\nTikTok → Gelen Kutusu → taslağı açın, başlığı yapıştırıp paylaşın.\n\n```\n{caption}\n```')
    return {'video_id': video_id, 'tiktok_publish_id': publish_id, 'status': 'drafted'}


def run(store, platform=platforms.DEFAULT, **kwargs):
    """Seçilen platformun yayınını çalıştırır. Yayıncı eklenmemiş platform hiçbir şey yapmadan hata verir."""
    try:
        target = platforms.get(platform)
    except ValueError as e:
        raise OpsError(str(e))
    publisher = PUBLISHERS.get(target.name)
    if publisher is None:
        raise OpsError(f'{target.label} yayıncısı henüz kurulu değil. Hiçbir şey paylaşılmadı.',
                       hint='Bu platform için yayın adımı henüz eklenmedi. Şimdilik yalnız Instagram yayınlanır.')
    return publisher(store, **kwargs)


PUBLISHERS = {'instagram': run_instagram, 'youtube': run_youtube, 'tiktok': run_tiktok}
