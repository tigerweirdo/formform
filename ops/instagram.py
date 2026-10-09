"""Instagram Graph API (Instagram Login). Paylaşım çağrıları (create/publish) asla yeniden denenmez."""
from . import config, net


def _auth(token):
    return {'Authorization': f'Bearer {token}'}


def _url(path):
    return f'{config.IG_API}/{config.IG_VERSION}/{path}'


def me(token):
    return net.request('GET', _url('me'), headers=_auth(token), query={'fields': 'username'}, retries=2)


def create_reel(token, video_url, caption):
    res = net.request('POST', _url(f'{config.IG_USER_ID}/media'), headers=_auth(token),
                      form={'media_type': 'REELS', 'video_url': video_url, 'caption': caption,
                            'share_to_feed': 'true'})
    if not isinstance(res, dict) or not res.get('id'):
        raise net.HttpError(200, 'media', f'yanıtta id yok: {res}')
    return str(res['id'])


def reel_status(token, creation_id):
    return net.request('GET', _url(creation_id), headers=_auth(token),
                       query={'fields': 'status_code,status'}, retries=2)


def publish_reel(token, creation_id):
    res = net.request('POST', _url(f'{config.IG_USER_ID}/media_publish'), headers=_auth(token),
                      form={'creation_id': creation_id})
    if not isinstance(res, dict) or not res.get('id'):
        raise net.HttpError(200, 'media_publish', f'yanıtta id yok: {res}')
    return str(res['id'])


def insights(token, media_id, metric):
    """Hata verirse {'error': ...} döner (silinmiş medya, geçici hata). Öğrenme akışı o videoyu atlar."""
    try:
        res = net.request('GET', _url(f'{media_id}/insights'), headers=_auth(token), query={'metric': metric},
                          retries=2)
        return res if isinstance(res, dict) else {'error': 'beklenmeyen yanıt'}
    except net.HttpError as e:
        return {'error': str(e)}


def refresh(token):
    return net.request('GET', f'{config.IG_API}/refresh_access_token',
                       query={'grant_type': 'ig_refresh_token', 'access_token': token}, retries=1)
