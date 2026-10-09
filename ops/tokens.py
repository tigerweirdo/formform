"""Instagram jetonu: GitHub gizli değeri `IG_ACCESS_TOKEN` ilk jetondur. Yenilenen jeton şifrelenip
`veri` dalında saklanır; anahtarı ilk jetondur. Gizli değer elle değiştirilirse yenisi kullanılır."""
import os
import re
from datetime import datetime, timedelta, timezone

from . import instagram
from .errors import TokenMissing, TokenRefreshFailed
from .net import add_secret
from .seal import seal, unseal


def validate(token):
    token = str(token or '').strip()
    if len(token) < 40 or re.search(r'\s', token):
        raise TokenMissing('Geçerli bir Instagram jetonu yok (IG_ACCESS_TOKEN)')
    return token


def bootstrap():
    """Gizli değerdeki ilk jeton. Yanlışlıkla yapıştırılan 'Bearer ' öneki atılır."""
    raw = str(os.environ.get('IG_ACCESS_TOKEN') or '').strip()
    raw = re.sub(r'^Bearer\s+', '', raw, flags=re.I)
    return validate(raw)


def current(data):
    """Kullanılacak jeton: şifreli yenilenmiş jeton açılıyorsa o, yoksa gizli değer."""
    boot = bootstrap()
    add_secret(boot)
    if data is not None and data.sealed_token:
        opened = unseal(boot, data.sealed_token)
        if opened:
            token = validate(opened)
            add_secret(token)
            return token
    return boot


def parse_refresh(res):
    """Yenileme yanıtını doğrular: (yeni jeton, saniye cinsinden süre). Geçersizse TokenRefreshFailed."""
    new = str((res or {}).get('access_token') or '').strip() if isinstance(res, dict) else ''
    try:
        expires = float((res or {}).get('expires_in')) if isinstance(res, dict) else float('nan')
    except (TypeError, ValueError):
        expires = float('nan')
    if len(new) < 40 or re.search(r'\s', new) or not (86400 <= expires < float('inf')):
        # Yanıtın kendisi hata metnine konmaz: içinde jeton olabilir.
        fields = ', '.join(res.keys()) if isinstance(res, dict) and res else 'boş'
        raise TokenRefreshFailed(f'Instagram yenileme yanıtı geçersiz, jeton değiştirilmedi (alanlar: {fields})',
                                 hint='Eski jeton süresi dolana kadar çalışır. TOKEN akışını elle çalıştırıp deneyin.')
    return new, expires


def refresh(store, now=None):
    """Jetonu Instagram'da yeniler, yanıtı doğrular, geçerliyse saklar. Geçersizse eski jeton kalır."""
    boot = bootstrap()
    add_secret(boot)
    token = current(store.read())
    try:
        res = instagram.refresh(token)
    except Exception as e:                      # ağ/HTTP hatası: jeton değişmez
        raise TokenRefreshFailed(f'Instagram yenileme çağrısı başarısız: {e}')
    new, expires = parse_refresh(res)
    add_secret(new)
    now = now or datetime.now(timezone.utc)
    sealed = seal(boot, new)
    stamp = now.strftime('%Y-%m-%dT%H:%M:%SZ')
    expires_at = (now + timedelta(seconds=expires)).strftime('%Y-%m-%dT%H:%M:%SZ')

    def save(data):
        data.sealed_token = sealed
        data.config['ig_token_updated_at'] = stamp
        data.config['ig_token_expires_at'] = expires_at

    store.transaction(save, 'Instagram jetonu yenilendi')
    return {'updated_at': stamp, 'expires_at': expires_at}
