"""Jetonu depoda düz metin tutmamak için basit kimlik doğrulamalı şifreleme (yalnız standart kütüphane).

Anahtar, GitHub gizli değeri olan IG_ACCESS_TOKEN'dan türer. Gizli değer değişirse eski şifreli kayıt
açılamaz ve yeni değer kullanılır: yani jetonu elle yenilemek kendiliğinden eskisini geçersiz kılar.
HMAC-SHA256 ile sayaç kipi akış şifresi + şifrele-sonra-doğrula (encrypt-then-MAC).
"""
import base64
import hashlib
import hmac
import os

_MAGIC = b'FF1'


def _keys(secret):
    root = hashlib.sha256(('formform-seal-v1|' + secret).encode('utf-8')).digest()
    return (hmac.new(root, b'enc', hashlib.sha256).digest(), hmac.new(root, b'mac', hashlib.sha256).digest())


def _stream(key, nonce, length):
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hmac.new(key, nonce + counter.to_bytes(8, 'big'), hashlib.sha256).digest()
        counter += 1
    return bytes(out[:length])


def seal(secret, plaintext):
    enc, mac = _keys(secret)
    nonce = os.urandom(16)
    data = plaintext.encode('utf-8')
    body = bytes(a ^ b for a, b in zip(data, _stream(enc, nonce, len(data))))
    tag = hmac.new(mac, _MAGIC + nonce + body, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(_MAGIC + nonce + body + tag).decode('ascii')


def unseal(secret, blob):
    """Açılırsa metni, anahtar yanlışsa ya da kayıt bozuksa None döndürür."""
    try:
        raw = base64.urlsafe_b64decode(blob.strip().encode('ascii'))
    except (ValueError, UnicodeEncodeError):
        return None
    if len(raw) < len(_MAGIC) + 16 + 32 or not raw.startswith(_MAGIC):
        return None
    nonce, body, tag = raw[3:19], raw[19:-32], raw[-32:]
    enc, mac = _keys(secret)
    if not hmac.compare_digest(tag, hmac.new(mac, _MAGIC + nonce + body, hashlib.sha256).digest()):
        return None
    try:
        return bytes(a ^ b for a, b in zip(body, _stream(enc, nonce, len(body)))).decode('utf-8')
    except UnicodeDecodeError:
        return None
