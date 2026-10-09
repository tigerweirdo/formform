"""YouTube Data API v3 (Shorts). Kimlik: Google OAuth yenileme jetonu.

Google yenileme jetonunu yenilemez (Instagram'ın aksine) ve süresi yoktur. İlk yetkilendirme bir kez elle yapılır,
sonra her çalışmada yenileme jetonu kısa ömürlü (1 saat) bir erişim jetonuna çevrilir. Gizli değerler:
YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN. İsteğe bağlı YT_PRIVACY (public, unlisted, private; varsayılan public).

Yükleme iki aşamalıdır: önce oturum açılır (video oluşmaz, zararsız), sonra dosya gönderilir (video oluşur). Oturumu
açan çağrı ile dosyayı gönderen çağrı asla yeniden denenmez; yalnız yan etkisiz okumalar (jeton, kanal) denenir.
Doğrudan dosya yüklendiği için Cloudinary gerekmez.
"""
import os
import re

from . import caption as captions
from . import config, net
from .errors import CredentialsMissing, OpsError, YoutubeAuthFailed

TITLE_MAX = 100
TAGS_MAX_CHARS = 400            # YouTube sınırı 500, pay bırakılır
CATEGORY = '1'                  # Film & Animation
PRIVACY = ('public', 'unlisted', 'private')


def credentials():
    names = ('YT_CLIENT_ID', 'YT_CLIENT_SECRET', 'YT_REFRESH_TOKEN')
    values = {n: str(os.environ.get(n) or '').strip() for n in names}
    missing = [n for n, v in values.items() if not v]
    if missing:
        raise CredentialsMissing(
            f"YouTube kimlik bilgisi eksik: {', '.join(missing)}",
            hint="Depo → Settings → Secrets and variables → Actions sayfasında YT_CLIENT_ID, YT_CLIENT_SECRET ve "
                 "YT_REFRESH_TOKEN gizli değerlerini tanımlayın (README.md). Hiçbir şey paylaşılmadı.")
    net.add_secret(values['YT_CLIENT_SECRET'])
    net.add_secret(values['YT_REFRESH_TOKEN'])
    return values


def privacy():
    value = str(os.environ.get('YT_PRIVACY') or 'public').strip().lower()
    if value not in PRIVACY:
        raise OpsError(f"YT_PRIVACY geçersiz: {value!r} ({', '.join(PRIVACY)} olmalı). Hiçbir şey paylaşılmadı.")
    return value


def access_token():
    """Yenileme jetonunu kısa ömürlü erişim jetonuna çevirir. Yan etkisizdir, yeniden denenir."""
    c = credentials()
    try:
        res = net.request('POST', config.YT_TOKEN_URL, retries=2,
                          form={'grant_type': 'refresh_token', 'client_id': c['YT_CLIENT_ID'],
                                'client_secret': c['YT_CLIENT_SECRET'], 'refresh_token': c['YT_REFRESH_TOKEN']})
    except net.HttpError as e:
        raise YoutubeAuthFailed(f'Google erişim jetonu alınamadı: {e}')
    token = str(res.get('access_token') or '').strip() if isinstance(res, dict) else ''
    if len(token) < 20 or re.search(r'\s', token):
        fields = ', '.join(res.keys()) if isinstance(res, dict) and res else 'boş'
        raise YoutubeAuthFailed(f'Google yanıtında geçerli erişim jetonu yok (alanlar: {fields})')
    net.add_secret(token)
    return token


def _auth(token):
    return {'Authorization': f'Bearer {token}'}


def channel(token):
    """Jetonun bağlı olduğu kanalın adı (kuru çalıştırmada hesabı doğrulamak için)."""
    res = net.request('GET', f'{config.YT_API}/youtube/v3/channels', headers=_auth(token),
                      query={'part': 'snippet', 'mine': 'true'}, retries=2)
    items = res.get('items') if isinstance(res, dict) else None
    if not items:
        raise YoutubeAuthFailed('Bu Google hesabında YouTube kanalı bulunamadı',
                                hint='Yetkilendirmeyi doğru Google hesabı ve kanalla yapın, kanalı olmayan hesap yükleyemez.')
    return str(((items[0] or {}).get('snippet') or {}).get('title') or '?')


def _clean(text):
    return re.sub(r'[<>]', '', str(text or ''))              # YouTube başlık ve açıklamada < > kabul etmez


def metadata(caption, privacy_status):
    """Instagram caption'ından YouTube başlığı, açıklaması ve etiketleri. Başlık: açılış cümlesi."""
    hook = _clean(captions.hook(caption)) or 'form/form'
    title = hook if len(hook) <= TITLE_MAX else hook[:TITLE_MAX - 1].rstrip() + '…'
    tags, used = ['formform'], len('formform')
    for t in captions.tags(caption):
        tag = _clean(t.lstrip('#'))
        if tag and tag not in tags and used + len(tag) + 1 <= TAGS_MAX_CHARS:
            tags.append(tag)
            used += len(tag) + 1
    return {
        'snippet': {'title': title, 'description': _clean(str(caption or '').strip()) + '\n\n#Shorts',
                    'tags': tags, 'categoryId': CATEGORY},
        'status': {'privacyStatus': privacy_status, 'selfDeclaredMadeForKids': False},
    }


def start_upload(token, size, meta):
    """Yükleme oturumunu açar, video henüz oluşmaz. Yeniden denenmez. Oturum adresini döndürür."""
    _, headers = net.request(
        'POST', f'{config.YT_API}/upload/youtube/v3/videos', with_headers=True,
        headers=dict(_auth(token), **{'X-Upload-Content-Length': str(size), 'X-Upload-Content-Type': 'video/mp4'}),
        query={'uploadType': 'resumable', 'part': 'snippet,status'}, json_body=meta)
    session = headers.get('location')
    if not session:
        raise net.HttpError(200, 'youtube/upload', 'yanıtta yükleme oturumu adresi yok')
    return session


def send_video(token, session_url, path):
    """Dosyayı gönderir, video bu çağrıyla oluşur. ASLA yeniden denenmez. YouTube video kimliğini döndürür."""
    with open(path, 'rb') as f:
        data = f.read()
    res = net.request('PUT', session_url, headers=_auth(token), body=data, content_type='video/mp4', timeout=600)
    if not isinstance(res, dict) or not res.get('id'):
        raise net.HttpError(200, 'youtube/upload', f'yanıtta video kimliği yok: {res}')
    return str(res['id'])
