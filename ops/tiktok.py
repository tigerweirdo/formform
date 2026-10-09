"""TikTok: Login Kit (yetkilendirme), Content Posting API TASLAK modu (Upload) ve Display API (istatistik).

Direct Post (otomatik paylaşım) KULLANILMAZ: TikTok her paylaşım için kullanıcının açık onayını ister. Video
kullanıcının TikTok gelen kutusuna taslak olarak yüklenir, kullanıcı TikTok uygulamasında düzenleyip kendisi
paylaşır. Başlık API ile gönderilemez (taslak modunda yok), kullanıcı uygulamada yapıştırır.

Jetonlar: erişim jetonu 24 saat, yenileme jetonu 365 gün geçerlidir ve HER YENİLEMEDE DEĞİŞEBİLİR (yeni değer
kullanılmak zorunda). Bu yüzden yenileme sonucu hemen şifreli kaydedilir (`tiktok_token.sealed`, veri dalında).
Şifreleme anahtarı GitHub gizli değeri TIKTOK_CLIENT_SECRET'tır, depoya hiç yazılmaz. Gizli değer değişirse kayıt
açılamaz ve yeniden bağlanmak gerekir.

Yan etkili çağrılar (yetkilendirme kodu değişimi, jeton yenileme, yükleme başlatma, dosya gönderme) asla yeniden
denenmez: kod tek kullanımlıktır, yenileme eski jetonu geçersiz kılabilir, yükleme çift taslak açabilir.
Yalnız okumalar (kullanıcı bilgisi, durum, video listesi) denenir.
"""
import json
import os
from datetime import datetime, timedelta, timezone

from . import config, net
from .errors import CredentialsMissing, OpsError, TiktokAuthFailed, TiktokNotConnected
from .seal import seal, unseal

REFRESH_MARGIN = timedelta(minutes=10)         # erişim jetonunun bitimine bu kadar kala yenilenir
READ_MARGIN = timedelta(minutes=1)             # yenilemeyen okuma: bitmesine bundan az kalan jeton kullanılmaz
VIDEO_FIELDS = 'id,title,create_time,duration,view_count,like_count,comment_count,share_count,video_description'


def credentials():
    values = {n: str(os.environ.get(n) or '').strip() for n in ('TIKTOK_CLIENT_KEY', 'TIKTOK_CLIENT_SECRET')}
    missing = [n for n, v in values.items() if not v]
    if missing:
        raise CredentialsMissing(
            f"TikTok kimlik bilgisi eksik: {', '.join(missing)}",
            hint="Depo → Settings → Secrets and variables → Actions sayfasında TIKTOK_CLIENT_KEY ve "
                 "TIKTOK_CLIENT_SECRET gizli değerlerini tanımlayın (README.md). Hiçbir şey paylaşılmadı.")
    net.add_secret(values['TIKTOK_CLIENT_SECRET'])
    return values


def _iso(moment):
    return moment.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _parse(text):
    return datetime.fromisoformat(str(text).replace('Z', '+00:00'))


def _api(res, what):
    """TikTok API yanıtı: 'error.code' ok değilse hata (HTTP 200 dönse bile)."""
    err = res.get('error') if isinstance(res, dict) else None
    if isinstance(err, dict) and err.get('code') not in (None, 'ok'):
        raise net.HttpError(200, what, f"{err.get('code')}: {err.get('message') or ''}".strip())
    if not isinstance(res, dict):
        raise net.HttpError(200, what, 'beklenmeyen yanıt')
    return res.get('data') or {}


# --- yetkilendirme -----------------------------------------------------------------------------------------------

def _tokens(res, now, previous=None):
    """Jeton yanıtını doğrular, saklanacak kayda çevirir. Yanıtın kendisi hata metnine konmaz (jeton olabilir)."""
    if not isinstance(res, dict) or not res.get('access_token') or not res.get('refresh_token'):
        detail = ''
        if isinstance(res, dict):
            detail = f" ({str(res.get('error') or '')} {str(res.get('error_description') or '')})".replace('  ', ' ')
        raise TiktokAuthFailed(f'TikTok yanıtında jeton yok{detail}')
    access, refresh = str(res['access_token']), str(res['refresh_token'])
    net.add_secret(access)
    net.add_secret(refresh)

    def seconds(key, default):
        try:
            value = float(res.get(key))
            return value if value > 0 else default
        except (TypeError, ValueError):
            return default
    return {
        'access_token': access, 'refresh_token': refresh,
        'access_expires_at': _iso(now + timedelta(seconds=seconds('expires_in', 86400))),
        'refresh_expires_at': _iso(now + timedelta(seconds=seconds('refresh_expires_in', 365 * 86400))),
        'open_id': str(res.get('open_id') or (previous or {}).get('open_id') or ''),
        'scope': str(res.get('scope') or (previous or {}).get('scope') or ''),
    }


def _token_call(form, now, previous=None):
    c = credentials()
    try:
        res = net.request('POST', f'{config.TIKTOK_API}/v2/oauth/token/',
                          form=dict(form, client_key=c['TIKTOK_CLIENT_KEY'], client_secret=c['TIKTOK_CLIENT_SECRET']))
    except net.HttpError as e:
        raise TiktokAuthFailed(f'TikTok jeton çağrısı başarısız: {e}')
    return _tokens(res, now, previous)


def exchange_code(code, now):
    """Yetkilendirme kodunu jetonlara çevirir. Kod tek kullanımlıktır: yeniden denenmez."""
    net.add_secret(code)                                   # kimlik bilgisidir: hata metinlerinde ve günlükte görünmez
    return _token_call({'grant_type': 'authorization_code', 'code': code, 'redirect_uri': config.TIKTOK_REDIRECT_URI}, now)


def refresh_tokens(tokens, now):
    """Yenileme jetonuyla yeni jeton alır. Yeni yenileme jetonu eskisinden farklı olabilir. Yeniden denenmez."""
    return _token_call({'grant_type': 'refresh_token', 'refresh_token': tokens['refresh_token']}, now, tokens)


# --- şifreli saklama ---------------------------------------------------------------------------------------------

def _key():
    return credentials()['TIKTOK_CLIENT_SECRET']


def load(data):
    """Kayıtlı jetonlar. Yoksa ya da açılamıyorsa (gizli değer değişmiş) TiktokNotConnected."""
    if not data.sealed_tiktok:
        raise TiktokNotConnected('TikTok hesabı bağlı değil (kayıtlı jeton yok)')
    opened = unseal(_key(), data.sealed_tiktok)
    try:
        tokens = json.loads(opened) if opened else None
    except ValueError:
        tokens = None
    if not isinstance(tokens, dict) or not tokens.get('refresh_token'):
        raise TiktokNotConnected('Kayıtlı TikTok jetonu açılamadı (TIKTOK_CLIENT_SECRET değişmiş olabilir)')
    net.add_secret(tokens.get('access_token'))
    net.add_secret(tokens['refresh_token'])
    return tokens


def save(store, tokens, now, display_name=None):
    """Jetonları şifreli yazar. Kapalı değerler (açık metin) yalnız şifreli blokta; config'e yalnız sır olmayanlar gider."""
    sealed = seal(_key(), json.dumps(tokens, ensure_ascii=False))

    def apply(data):
        data.sealed_tiktok = sealed
        cfg = data.config
        cfg['tiktok_open_id'] = tokens.get('open_id', '')
        cfg['tiktok_scope'] = tokens.get('scope', '')
        cfg['tiktok_access_expires_at'] = tokens['access_expires_at']
        cfg['tiktok_refresh_expires_at'] = tokens['refresh_expires_at']
        cfg['tiktok_updated_at'] = _iso(now)
        if display_name:
            cfg['tiktok_display_name'] = display_name
    store.transaction(apply, 'TikTok jetonu kaydedildi')


def access_token(store, now, margin=REFRESH_MARGIN):
    """Geçerli erişim jetonu. Bitmesine `margin` kalmışsa yenilenir ve sonuç HEMEN kaydedilir (yenileme jetonu
    değişebilir). Yenileme yalnız TikTok workflow'unda yapılır (tek eşzamanlılık grubu: iki yenileme yarışmasın)."""
    data = store.read()
    tokens = load(data)
    if _parse(tokens['access_expires_at']) - now > margin:
        return tokens['access_token']
    if _parse(tokens['refresh_expires_at']) <= now:
        raise TiktokAuthFailed('TikTok yenileme jetonunun süresi dolmuş, siteden yeniden bağlanın')
    fresh = refresh_tokens(tokens, now)
    try:
        save(store, fresh, now)
    except OpsError as e:
        raise TiktokAuthFailed(
            f'TikTok jetonu yenilendi ama kaydedilemedi: {e}',
            hint="Yenileme jetonu değişmiş olabilir ve eskisi geçersizdir. Siteden yeniden bağlanın "
                 "(Connect TikTok), sonra işi yeniden çalıştırın.")
    return fresh['access_token']


def stored_access_token(data, now):
    """Kayıtlı erişim jetonu, hâlâ geçerliyse. Süresi dolmuşsa None. ASLA yenilemez ve hiçbir şey yazmaz.

    Öğrenme turu bunu kullanır: o tur TikTok workflow'unun eşzamanlılık grubunda değildir, yenileme yapsaydı aynı
    anda çalışan bir yükleme ile yarışıp yenileme jetonunu bozabilirdi. Jetonu taze tutan yükleme turudur."""
    tokens = load(data)
    if not tokens.get('access_token') or _parse(tokens['access_expires_at']) - now <= READ_MARGIN:
        return None
    return tokens['access_token']


def connect(store, code, now, replace=False, log=print):
    """Siteden gelen yetkilendirme kodunu jetona çevirip kaydeder. Bağlı hesap varsa başka hesap sessizce
    onun yerine geçmez: o hesabın kimliği (open_id) farklıysa `replace` istenir."""
    tokens = exchange_code(code, now)
    who = user_info(tokens['access_token'])
    open_id = who.get('open_id') or tokens.get('open_id')
    tokens['open_id'] = open_id or ''
    try:
        existing = load(store.read())
    except TiktokNotConnected:
        existing = None
    if existing and existing.get('open_id') and existing['open_id'] != tokens['open_id'] and not replace:
        raise TiktokAuthFailed(
            'Bu TikTok hesabı, bağlı olan hesaptan farklı. Hesabı değiştirmek için workflow\'u "replace" ile çalıştırın.',
            hint="Bilmediğiniz biri bağlanmaya çalışmış olabilir. Hesabı gerçekten değiştirmek istiyorsanız "
                 "FORMFORM tiktok workflow'unu 'replace' işaretli çalıştırın, yoksa hiçbir şey yapmayın.")
    save(store, tokens, now, display_name=who.get('display_name') or '')
    log(f"TikTok bağlandı: {who.get('display_name') or '?'} (yetkiler: {tokens['scope'] or '?'})")
    return {'display_name': who.get('display_name') or '', 'open_id': tokens['open_id'], 'scope': tokens['scope']}


# --- API ---------------------------------------------------------------------------------------------------------

def _auth(token):
    return {'Authorization': f'Bearer {token}'}


def user_info(token):
    res = net.request('GET', f'{config.TIKTOK_API}/v2/user/info/', headers=_auth(token),
                      query={'fields': 'open_id,display_name'}, retries=2)
    return _api(res, 'tiktok/user/info').get('user') or {}


def init_upload(token, size):
    """Gelen kutusuna taslak yüklemeyi başlatır (tek parça). Yeniden denenmez. (publish_id, yükleme adresi)."""
    if size > config.TIKTOK_MAX_SINGLE_CHUNK:
        raise OpsError(f'Video {size // (1024 * 1024)} MB: tek parça sınırını ({config.TIKTOK_MAX_SINGLE_CHUNK // (1024 * 1024)} MB) '
                       'aşıyor, parçalı yükleme henüz yazılmadı. Hiçbir şey yüklenmedi.')
    res = net.request('POST', f'{config.TIKTOK_API}/v2/post/publish/inbox/video/init/', headers=_auth(token),
                      json_body={'source_info': {'source': 'FILE_UPLOAD', 'video_size': size, 'chunk_size': size,
                                                 'total_chunk_count': 1}})
    data = _api(res, 'tiktok/inbox/init')
    if not data.get('publish_id') or not data.get('upload_url'):
        raise net.HttpError(200, 'tiktok/inbox/init', 'yanıtta publish_id ya da upload_url yok')
    return str(data['publish_id']), str(data['upload_url'])


def send_video(upload_url, path):
    """Dosyayı gönderir (taslak bu çağrıyla oluşur). ASLA yeniden denenmez. Yükleme adresinde izin anahtarı vardır,
    hata metninden sorgu dizisi zaten silinir."""
    with open(path, 'rb') as f:
        data = f.read()
    last = len(data) - 1
    net.request('PUT', upload_url, body=data, content_type='video/mp4', timeout=600,
                headers={'Content-Range': f'bytes 0-{last}/{len(data)}'})


def publish_status(token, publish_id):
    """Yükleme durumu: PROCESSING_UPLOAD, SEND_TO_USER_INBOX (taslak geldi), PUBLISH_COMPLETE, FAILED."""
    res = net.request('POST', f'{config.TIKTOK_API}/v2/post/publish/status/fetch/', headers=_auth(token),
                      json_body={'publish_id': publish_id}, retries=2)
    return _api(res, 'tiktok/status')


def list_videos(token, limit=20):
    """Kullanıcının kendi videoları ve sayıları (en yeniden eskiye). Yalnız okuma.

    TikTok sayfa başına en çok 20 video verir. `limit` daha büyükse sonraki sayfalar `cursor` ile istenir."""
    limit = max(1, int(limit))
    out, cursor = [], None
    while len(out) < limit:
        body = {'max_count': min(limit - len(out), 20)}
        if cursor is not None:
            body['cursor'] = cursor
        res = net.request('POST', f'{config.TIKTOK_API}/v2/video/list/', headers=_auth(token),
                          query={'fields': VIDEO_FIELDS}, json_body=body, retries=2)
        data = _api(res, 'tiktok/video/list')
        page = data.get('videos') or []
        out.extend(page)
        cursor = data.get('cursor')
        if not page or not data.get('has_more') or cursor is None:
            break
    return out[:limit]
