"""Küçük HTTP istemcisi (yalnız standart kütüphane).

- Yeniden deneme varsayılan olarak KAPALI. Yalnız zararsız okuma/yükleme çağrılarında `retries` verilir;
  paylaşım, üstlenme gibi yan etkili çağrılar asla yeniden denenmez.
- Gizli değerler (`add_secret`) hata metinlerinden ve günlüklerden silinir.
"""
import json
import mimetypes
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

_SECRETS = set()


def add_secret(value):
    value = str(value or '')
    if len(value) >= 8:
        _SECRETS.add(value)
    return value


def scrub(text):
    text = str(text)
    for secret in sorted(_SECRETS, key=len, reverse=True):
        text = text.replace(secret, '***')
    return text


class HttpError(Exception):
    def __init__(self, status, url, body):
        self.status = status
        self.body = scrub(body)[:800]
        self.url = scrub(url.split('?')[0])
        super().__init__(f'HTTP {status} {self.url}: {self.body}')


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _open(req, timeout, follow=True):
    opener = urllib.request.build_opener() if follow else urllib.request.build_opener(_NoRedirect)
    return opener.open(req, timeout=timeout)


def encode_multipart(fields, files=None):
    """fields: {ad: değer}, files: {ad: (dosya_adı, bayt, tür)} → (gövde, content_type)."""
    boundary = '----formform' + uuid.uuid4().hex
    parts = []
    for name, value in (fields or {}).items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    for name, (filename, data, ctype) in (files or {}).items():
        ctype = ctype or mimetypes.guess_type(filename)[0] or 'application/octet-stream'
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                      f'Content-Type: {ctype}\r\n\r\n').encode() + data + b'\r\n')
    parts.append(f'--{boundary}--\r\n'.encode())
    return b''.join(parts), f'multipart/form-data; boundary={boundary}'


def request(method, url, *, headers=None, query=None, json_body=None, form=None, multipart=None,
            body=None, content_type=None, with_headers=False, timeout=60, retries=0, backoff=2.0):
    """JSON döndüren çağrı. Hata durumunda HttpError. Yanıt JSON değilse metin döner.

    body: ham bayt gövdesi (ör. video, content_type ile). with_headers: (yanıt, küçük harfli başlıklar) döner."""
    if query:
        url += ('&' if '?' in url else '?') + urllib.parse.urlencode(query)
    data = None
    hdrs = dict(headers or {})
    if json_body is not None:
        data = json.dumps(json_body).encode()
        hdrs['Content-Type'] = 'application/json'
    elif form is not None:
        data = urllib.parse.urlencode(form).encode()
        hdrs['Content-Type'] = 'application/x-www-form-urlencoded'
    elif multipart is not None:
        data, hdrs['Content-Type'] = encode_multipart(*multipart)
    elif body is not None:
        data, hdrs['Content-Type'] = body, content_type or 'application/octet-stream'

    last = None
    raw = ''
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        got = {}
        try:
            with _open(req, timeout) as resp:
                raw = resp.read().decode('utf-8', 'replace')
                got = {k.lower(): v for k, v in resp.headers.items()}
            parsed = json.loads(raw) if raw.strip() else {}
            return (parsed, got) if with_headers else parsed
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8', 'replace')
            last = HttpError(e.code, url, body)
            if e.code < 500 and e.code != 429:      # istemci hatası tekrarla düzelmez
                raise last
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last = HttpError(0, url, f'bağlantı hatası: {e}')
        except ValueError:                              # JSON değil
            return (raw, got) if with_headers else raw
        if attempt < retries:
            time.sleep(backoff * (2 ** attempt))
    raise last


def download(url, dest, *, headers=None, timeout=300, retries=0, backoff=2.0):
    """Dosya indirir. 302 yönlendirmesinde Authorization başlığı yeni adrese GİTMEZ
    (GitHub release dosyaları imzalı bir depolama adresine yönlenir, ek kimlik doğrulama onu bozar)."""
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=dict(headers or {}))
            try:
                resp = _open(req, timeout, follow=False)
            except urllib.error.HTTPError as e:
                if e.code in (301, 302, 303, 307, 308) and e.headers.get('Location'):
                    target = urllib.parse.urljoin(url, e.headers['Location'])
                    resp = _open(urllib.request.Request(target), timeout)
                else:
                    raise
            tmp = dest + '.part'
            with resp, open(tmp, 'wb') as f:
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
            os.replace(tmp, dest)
            return dest
        except urllib.error.HTTPError as e:
            last = HttpError(e.code, url, e.read().decode('utf-8', 'replace'))
            if e.code < 500 and e.code != 429:
                raise last
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last = HttpError(0, url, f'bağlantı hatası: {e}')
        if attempt < retries:
            time.sleep(backoff * (2 ** attempt))
    raise last
