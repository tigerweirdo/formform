"""Cloudinary: Instagram videoyu herkese açık bir adresten çeker. Yükleme imzalıdır (API anahtarı + gizli anahtar)."""
import hashlib
import os
import time

from . import config, net


def sign(params, secret):
    to_sign = '&'.join(f'{k}={params[k]}' for k in sorted(params) if params[k] not in (None, ''))
    return hashlib.sha1((to_sign + secret).encode('utf-8')).hexdigest()


def credentials():
    key = os.environ.get('CLOUDINARY_API_KEY', '').strip()
    secret = os.environ.get('CLOUDINARY_API_SECRET', '').strip()
    if not key or not secret:
        raise ValueError('CLOUDINARY_API_KEY ve CLOUDINARY_API_SECRET gizli değerleri tanımlı değil')
    net.add_secret(secret)
    return key, secret


def upload_video(path, public_id):
    key, secret = credentials()
    ts = str(int(time.time()))
    signed = {'public_id': public_id, 'timestamp': ts}
    fields = dict(signed, api_key=key, signature=sign(signed, secret))
    with open(path, 'rb') as f:
        data = f.read()
    res = net.request('POST', f'{config.CLOUDINARY_API}/{config.CLOUD_NAME}/video/upload',
                      multipart=(fields, {'file': (os.path.basename(path), data, 'video/mp4')}),
                      timeout=300, retries=2)
    if not isinstance(res, dict) or not res.get('secure_url'):
        raise net.HttpError(200, 'cloudinary/upload', f'yanıtta secure_url yok: {res}')
    return res


def destroy(public_id):
    """Paylaşım bittikten sonra depolamayı temiz tutar. Hata verirse yayın etkilenmez."""
    try:
        key, secret = credentials()
        ts = str(int(time.time()))
        signed = {'public_id': public_id, 'timestamp': ts}
        fields = dict(signed, api_key=key, signature=sign(signed, secret))
        net.request('POST', f'{config.CLOUDINARY_API}/{config.CLOUD_NAME}/video/destroy', form=fields)
        return True
    except Exception:
        return False
