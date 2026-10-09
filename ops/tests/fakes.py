"""Yerel sahte GitHub + Instagram + Cloudinary sunucusu ve test ortamı.

Gerçek ağ kullanılmaz. Sunucu her isteği kaydeder (`calls`), böylece testler çağrı SIRASINI ve SAYISINI
doğrulayabilir (ör. 'üstlenme paylaşımdan önce', 'paylaşım çağrısı asla ikinci kez yapılmaz').
"""
import json
import os
import re
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from ops import config, net

TOKEN = 'IGAA' + 'a1B2c3D4' * 8                 # 36 karakter ek: geçerli uzunlukta sahte jeton
GH_TOKEN = 'ghs_' + 'z' * 36
CLOUD_SECRET = 'cloud-secret-0123456789'
YT_CLIENT_ID = 'fake-client-id.apps.googleusercontent.com'
YT_CLIENT_SECRET = 'GOCSPX-fake-client-secret-0123456789'
YT_REFRESH = '1//0gFakeRefreshTokenValue0123456789abcdef'
YT_ACCESS = 'ya29.FakeAccessTokenValue0123456789abcdefghij'
TT_CLIENT_KEY = 'awfakeclientkey0123'
TT_CLIENT_SECRET = 'fake-tiktok-client-secret-0123456789abcdef'
TT_CODE = 'fakeauthcode.0123456789abcdefghij'
VIDEO_BYTES = b'\x00\x00\x00\x18ftypmp42' + bytes(range(256)) * 40


class Call:
    def __init__(self, method, path, query, headers, body):
        self.method, self.path, self.query, self.headers, self.body = method, path, query, headers, body

    def json(self):
        return json.loads(self.body.decode() or '{}')

    def form(self):
        from urllib.parse import parse_qs
        return {k: v[0] for k, v in parse_qs(self.body.decode()).items()}

    def __repr__(self):
        return f'{self.method} {self.path}'


class FakeServer:
    """routes: [(METHOD, regex, fn(call, match) -> (status, body, headers))] ilk eşleşen çalışır."""

    def __init__(self):
        self.calls, self.routes = [], []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def handle_any(self):
                from urllib.parse import urlsplit, parse_qs
                parts = urlsplit(self.path)
                n = int(self.headers.get('Content-Length') or 0)
                body = self.rfile.read(n) if n else b''
                call = Call(self.command, parts.path, {k: v[0] for k, v in parse_qs(parts.query).items()},
                            dict(self.headers), body)
                outer.calls.append(call)
                for method, rx, fn in outer.routes:
                    m = re.fullmatch(rx, parts.path)
                    if method == self.command and m:
                        result = fn(call, m)                       # tek sefer: yan etkili olabilir
                        status, payload, headers = result if len(result) == 3 else (*result, {})
                        break
                else:
                    status, payload, headers = 404, {'error': f'rota yok: {self.command} {parts.path}'}, {}
                raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = handle_any

        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), H)
        self.base = f'http://127.0.0.1:{self.httpd.server_address[1]}'
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def route(self, method, regex, fn):
        self.routes.insert(0, (method, regex, fn))

    def named(self, method, regex):
        return [c for c in self.calls if c.method == method and re.fullmatch(regex, c.path)]

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def release(i, name=None, created='2026-10-02T03:30:00Z', payload=None, assets=True):
    payload = payload or {
        'video_id': f'FF_20261002_06{i:02d}00', 'structure': 'spiral form', 'palette': 'violet-pink',
        'caption': 'A temporary geometry.\n\nform/form — study.\n\n#formform #abstractart', 'loop': 'seamless',
        'file_name': 'x.mp4', 'duration': 8, 'fps': 30,
        'metadata': {'created_at': '2026-10-02T03:30:00.000Z', 'structure': 'spiral form', 'palette': 'violet-pink',
                     'materials': ['a', 'b'], 'transformations': ['t1'], 'motion_quality': 'm', 'lighting': 'l',
                     'loop_strategy': 'seamless_8s', 'selection_mode': 'exploration', 'structure_weight': 1,
                     'palette_weight': 1.2, 'motion_weight': 1, 'lighting_weight': 1}}
    return {
        'id': 500 + i, 'name': name or f"render {payload['video_id']}", 'draft': False,
        'tag_name': f"render-{payload['video_id']}", 'created_at': created, 'body': json.dumps(payload),
        'assets': [{'name': 'video.mp4', 'state': 'uploaded', 'url': f'/gh/repos/o/r/releases/assets/{900 + i}'}]
        if assets else [],
    }


class World:
    """Sahte GitHub/Instagram/Cloudinary + yerel veri deposu. `with World() as w:` biçiminde kullanılır."""

    def __init__(self, releases=None, env=None):
        self.srv = FakeServer()
        self.releases = {r['id']: r for r in (releases or [])}
        self.issues = []
        self.media_status = ['FINISHED']
        self.create_status = 200
        self.publish_status = 200
        self.claim_status = 200
        self.yt_token_status = 200
        self.yt_start_status = 200
        self.yt_upload_status = 200
        self.yt_uploaded = []                 # PUT ile gelen video gövdeleri
        self.tt_user = {'open_id': 'OPENID_OWNER', 'display_name': 'formform.studio'}
        self.tt_token_status = 200
        self.tt_init_status = 200
        self.tt_upload_status = 201
        self.tt_init_plan = []                         # çağrı başına durumlar (boşalınca tt_init_status)
        self.tt_upload_plan = []
        self.tt_statuses = ['SEND_TO_USER_INBOX']      # durum sorgusunun sırayla döneceği değerler (sonuncusu kalır)
        self.tt_fail_reason = 'file_format_check_failed'
        self.tt_rotate = False                         # yenilemede yenileme jetonu değişsin mi
        self.tt_videos = []
        self.tt_posts = {}                             # publish_id → durum yanıtı (sözlük) ya da HTTP hata kodu (sayı)
        self.tt_uploaded = []
        self.tt_expires_in = 86400
        self._tt_n = 0                                 # üretilen jeton sayısı
        self._tt_valid = set()                         # geçerli erişim jetonları
        self._tt_refresh = None                        # geçerli yenileme jetonu
        self._tt_used_codes = set()
        self.insights = {}
        self.wf_runs = {}                              # workflow dosyası → çalışmalar (en yeni başta)
        self.all_runs = []                             # ay içi bütün çalışmalar (bütçe bekçisi)
        self.runs_status = 200
        self.dispatch_status = 204
        self.dispatched = []                           # (workflow dosyası, gövde)
        self.dispatch_now = '2026-10-07T07:25:30Z'     # başlatılan çalışmanın görüneceği an
        self.refresh_response = {'access_token': 'IGAA' + 'N3w' * 20, 'token_type': 'bearer', 'expires_in': 5183944}
        self.extra_env = env or {}
        self._tmp = tempfile.mkdtemp(prefix='ff-world-')
        self.remote = os.path.join(self._tmp, 'veri.git')
        subprocess.run(['git', 'init', '-q', '--bare', self.remote], check=True)
        self._wire()

    # -- yönlendirme -----------------------------------------------------------
    def _wire(self):
        s, w = self.srv, self

        def list_releases(c, m):
            out = []
            for r in w.releases.values():
                r = json.loads(json.dumps(r))
                for a in r['assets']:
                    a['url'] = w.srv.base + a['url']
                out.append(r)
            return 200, out

        def rename(c, m):
            if w.claim_status != 200 and 'claimed' in c.json().get('name', ''):
                return w.claim_status, {'message': 'hata'}
            w.releases[int(m.group(1))]['name'] = c.json()['name']
            return 200, {'ok': True}

        s.route('GET', r'/gh/repos/o/r/releases', list_releases)
        s.route('PATCH', r'/gh/repos/o/r/releases/(\d+)', rename)
        s.route('GET', r'/gh/repos/o/r/releases/assets/\d+',
                lambda c, m: (302, b'', {'Location': w.srv.base + '/blob/video.mp4'}))
        s.route('GET', r'/blob/video.mp4', lambda c, m: (400, {'error': 'Authorization gitmemeli'}) if
                any(k.lower() == 'authorization' for k in c.headers) else (200, VIDEO_BYTES, {}))
        s.route('GET', r'/gh/repos/o/r/issues', lambda c, m: (200, [i for i in w.issues if i['state'] == 'open']))
        s.route('POST', r'/gh/repos/o/r/issues', self._new_issue)
        s.route('POST', r'/gh/repos/o/r/issues/(\d+)/comments', self._comment)
        s.route('PATCH', r'/gh/repos/o/r/issues/(\d+)', self._patch_issue)

        s.route('GET', r'/gh/repos/o/r/actions/workflows/([\w.-]+)/runs', self._wf_runs)
        s.route('POST', r'/gh/repos/o/r/actions/workflows/([\w.-]+)/dispatches', self._dispatch)
        s.route('GET', r'/gh/repos/o/r/actions/runs', self._runs_created)

        s.route('GET', r'/ig/v23.0/me', lambda c, m: (200, {'username': 'formform'}))
        s.route('POST', r'/ig/v23.0/\d+/media', lambda c, m: (w.create_status, {'id': 'CR1'} if w.create_status == 200
                                                           else {'error': {'message': 'create hatası'}}))
        s.route('GET', r'/ig/v23.0/CR1', self._status)
        s.route('POST', r'/ig/v23.0/\d+/media_publish', lambda c, m: (w.publish_status, {'id': 'MEDIA777'}
                                                                    if w.publish_status == 200 else {'error': {}}))
        s.route('GET', r'/ig/v23.0/(\w+)/insights', self._insights)
        s.route('GET', r'/ig/refresh_access_token', lambda c, m: (200, w.refresh_response))

        s.route('POST', r'/tt/v2/oauth/token/', self._tt_token)
        s.route('GET', r'/tt/v2/user/info/', self._tt_user)
        s.route('POST', r'/tt/v2/post/publish/inbox/video/init/', self._tt_init)
        s.route('PUT', r'/tt-upload/abc', self._tt_put)
        s.route('POST', r'/tt/v2/post/publish/status/fetch/', self._tt_status)
        s.route('POST', r'/tt/v2/video/list/', self._tt_list)
        s.route('POST', r'/ytoauth/token', self._yt_token)
        s.route('GET', r'/yt/youtube/v3/channels', lambda c, m: (200, {'items': [{'snippet': {'title': 'form/form'}}]}))
        s.route('POST', r'/yt/upload/youtube/v3/videos', self._yt_start)
        s.route('PUT', r'/yt/upload/session/(\w+)', self._yt_put)

        s.route('POST', r'/cld/[^/]+/video/upload', lambda c, m: (200, {'secure_url': f'{w.srv.base}/cdn/up.mp4',
                                                                       'public_id': 'x'}))
        s.route('POST', r'/cld/[^/]+/video/destroy', lambda c, m: (200, {'result': 'ok'}))

    def _wf_runs(self, c, m):
        if self.runs_status != 200:
            return self.runs_status, {'message': 'hata'}
        return 200, {'total_count': len(self.wf_runs.get(m.group(1), [])), 'workflow_runs': self.wf_runs.get(m.group(1), [])}

    def _dispatch(self, c, m):
        if self.dispatch_status != 204:
            return self.dispatch_status, {'message': 'Workflow does not have workflow_dispatch trigger'}
        self.dispatched.append((m.group(1), c.json()))
        # GitHub gibi: başlatılan tur hemen listede görünür (henüz bitmemiş), ikinci nöbet onu sayar
        self.wf_runs.setdefault(m.group(1), []).insert(0, {'created_at': self.dispatch_now, 'status': 'queued',
                                                            'conclusion': None, 'display_title': 'nöbet'})
        return 204, b''

    def _runs_created(self, c, m):
        if self.runs_status != 200:
            return self.runs_status, {'message': 'hata'}
        first, last = c.query.get('created', '..').split('..')
        hit = [r for r in self.all_runs if first <= r['created_at'][:10] <= last]
        page, size = int(c.query.get('page', 1)), int(c.query.get('per_page', 30))
        return 200, {'total_count': len(hit), 'workflow_runs': hit[(page - 1) * size:page * size]}

    def _tt_new_tokens(self, code_grant):
        self._tt_n += 1
        access = f'act.fake{self._tt_n}.AccessTokenValue0123456789abcdef'
        if code_grant or self.tt_rotate or self._tt_refresh is None:
            self._tt_refresh = f'rft.fake{self._tt_n}.RefreshTokenValue0123456789abcdef'
        self._tt_valid.add(access)
        return {'access_token': access, 'expires_in': self.tt_expires_in, 'open_id': self.tt_user['open_id'],
                'refresh_token': self._tt_refresh, 'refresh_expires_in': 31536000, 'scope': config.TIKTOK_SCOPES,
                'token_type': 'Bearer'}

    def _tt_token(self, c, m):
        form = c.form()
        if self.tt_token_status != 200:
            return self.tt_token_status, {'error': 'invalid_grant', 'error_description': 'bad', 'echo': form.get('client_secret'),
                                          'code': form.get('code')}
        if form.get('client_key') != TT_CLIENT_KEY or form.get('client_secret') != TT_CLIENT_SECRET:
            return 401, {'error': 'invalid_client', 'echo': form.get('client_secret')}
        if form.get('grant_type') == 'authorization_code':
            if form.get('code') in self._tt_used_codes or not str(form.get('code') or '').startswith('fakeauthcode'):
                return 400, {'error': 'invalid_grant', 'error_description': 'Authorization code is invalid or used'}
            if form.get('redirect_uri') != config.TIKTOK_REDIRECT_URI:
                return 400, {'error': 'invalid_request', 'error_description': 'redirect_uri mismatch'}
            self._tt_used_codes.add(form['code'])
            return 200, self._tt_new_tokens(True)
        if form.get('grant_type') == 'refresh_token' and form.get('refresh_token') == self._tt_refresh:
            return 200, self._tt_new_tokens(False)
        return 400, {'error': 'invalid_grant', 'error_description': 'refresh token invalid'}

    def _tt_auth(self, c):
        return c.headers.get('Authorization', '').replace('Bearer ', '', 1) in self._tt_valid

    def _tt_user(self, c, m):
        if not self._tt_auth(c):
            return 401, {'error': {'code': 'access_token_invalid', 'message': 'bad token', 'log_id': 'x'}}
        return 200, {'data': {'user': dict(self.tt_user)}, 'error': {'code': 'ok', 'message': '', 'log_id': 'x'}}

    def _tt_init(self, c, m):
        if not self._tt_auth(c):
            return 401, {'error': {'code': 'access_token_invalid', 'message': 'bad token', 'log_id': 'x'}}
        code = self.tt_init_plan.pop(0) if self.tt_init_plan else self.tt_init_status
        if code != 200:
            return code, {'error': {'code': 'internal_error', 'message': 'boom', 'log_id': 'x'}}
        return 200, {'data': {'publish_id': 'v_inbox_file~v2.123', 'upload_url': f'{self.srv.base}/tt-upload/abc?upload_token=SECRETUPLOADTOKEN'},
                     'error': {'code': 'ok', 'message': '', 'log_id': 'x'}}

    def _tt_put(self, c, m):
        code = self.tt_upload_plan.pop(0) if self.tt_upload_plan else self.tt_upload_status
        if code not in (200, 201, 206):
            return code, None                               # TikTok'un 404'ü gövdede düz 'null' döndürdü
        self.tt_uploaded.append((c.headers.get('Content-Range'), c.body))
        return code, {}

    def _tt_status(self, c, m):
        if not self._tt_auth(c):
            return 401, {'error': {'code': 'access_token_invalid', 'message': 'bad token', 'log_id': 'x'}}
        known = self.tt_posts.get(c.json().get('publish_id'))
        if isinstance(known, int):
            return known, {'error': {'code': 'internal_error', 'message': 'boom', 'log_id': 'x'}}
        if known is not None:
            return 200, {'data': dict(known), 'error': {'code': 'ok', 'message': '', 'log_id': 'x'}}
        code = self.tt_statuses.pop(0) if len(self.tt_statuses) > 1 else self.tt_statuses[0]
        data = {'status': code}
        if code == 'FAILED':
            data['fail_reason'] = self.tt_fail_reason
        return 200, {'data': data, 'error': {'code': 'ok', 'message': '', 'log_id': 'x'}}

    def _tt_list(self, c, m):
        if not self._tt_auth(c):
            return 401, {'error': {'code': 'access_token_invalid', 'message': 'bad token', 'log_id': 'x'}}
        body = c.json()
        start = int(body.get('cursor') or 0)                 # gerçek TikTok zaman damgası verir, sahtesi sıra numarası
        page = self.tt_videos[start:start + int(body.get('max_count') or 10)]
        return 200, {'data': {'videos': list(page), 'cursor': start + len(page),
                              'has_more': start + len(page) < len(self.tt_videos)},
                     'error': {'code': 'ok', 'message': '', 'log_id': 'x'}}

    def _yt_token(self, c, m):
        form = c.form()
        if self.yt_token_status != 200:
            return self.yt_token_status, {'error': 'invalid_grant', 'error_description': 'Token has been expired or revoked.'}
        ok = (form.get('grant_type') == 'refresh_token' and form.get('refresh_token') == YT_REFRESH
              and form.get('client_id') == YT_CLIENT_ID and form.get('client_secret') == YT_CLIENT_SECRET)
        if not ok:
            return 400, {'error': 'invalid_grant', 'echo': form.get('refresh_token')}
        return 200, {'access_token': YT_ACCESS, 'expires_in': 3599, 'token_type': 'Bearer'}

    def _yt_start(self, c, m):
        if c.headers.get('Authorization') != f'Bearer {YT_ACCESS}':
            return 401, {'error': {'message': 'Login Required'}}
        if self.yt_start_status != 200:
            return self.yt_start_status, {'error': {'message': 'quotaExceeded'}}
        return 200, {}, {'Location': f'{self.srv.base}/yt/upload/session/S1?upload_id=SECRETUPLOADID'}

    def _yt_put(self, c, m):
        if c.headers.get('Authorization') != f'Bearer {YT_ACCESS}':
            return 401, {'error': {'message': 'Login Required'}}
        if self.yt_upload_status != 200:
            return self.yt_upload_status, {'error': {'message': 'backendError'}}
        self.yt_uploaded.append(c.body)
        return 200, {'kind': 'youtube#video', 'id': 'YTVID1'}

    def _status(self, c, m):
        code = self.media_status.pop(0) if len(self.media_status) > 1 else self.media_status[0]
        return 200, {'status_code': code, 'status': 'detay' if code in ('ERROR', 'EXPIRED') else ''}

    def _insights(self, c, m):
        return self.insights.get((m.group(1), c.query.get('metric', '')), (200, {'error': {'message': 'yok'}}))

    def _new_issue(self, c, m):
        data = c.json()
        issue = dict(data, number=len(self.issues) + 1, state='open', updated_at='2026-10-02T05:00:00Z',
                     html_url=f'{self.srv.base}/issues/{len(self.issues) + 1}', comments=[])
        self.issues.append(issue)
        return 201, issue

    def _comment(self, c, m):
        issue = next(i for i in self.issues if i['number'] == int(m.group(1)))
        issue['comments'].append(c.json()['body'])
        return 201, {}

    def _patch_issue(self, c, m):
        issue = next(i for i in self.issues if i['number'] == int(m.group(1)))
        issue.update(c.json())
        return 200, issue

    # -- ortam -----------------------------------------------------------------
    def __enter__(self):
        env = {'GITHUB_TOKEN': GH_TOKEN, 'IG_ACCESS_TOKEN': TOKEN, 'CLOUDINARY_API_KEY': '123456',
               'CLOUDINARY_API_SECRET': CLOUD_SECRET, 'FORMFORM_STORE_REMOTE': 'file://' + self.remote,
               'RUNNER_TEMP': self._tmp, 'GITHUB_REPOSITORY_OWNER': 'o',
               'YT_CLIENT_ID': YT_CLIENT_ID, 'YT_CLIENT_SECRET': YT_CLIENT_SECRET, 'YT_REFRESH_TOKEN': YT_REFRESH,
               'TIKTOK_CLIENT_KEY': TT_CLIENT_KEY, 'TIKTOK_CLIENT_SECRET': TT_CLIENT_SECRET,
               **self.extra_env}
        self._patches = [
            mock.patch.dict(os.environ, env),
            mock.patch.object(config, 'REPO', 'o/r'),
            mock.patch.object(config, 'GITHUB_API', self.srv.base + '/gh'),
            mock.patch.object(config, 'IG_API', self.srv.base + '/ig'),
            mock.patch.object(config, 'CLOUDINARY_API', self.srv.base + '/cld'),
            mock.patch.object(config, 'YT_API', self.srv.base + '/yt'),
            mock.patch.object(config, 'TIKTOK_API', self.srv.base + '/tt'),
            mock.patch.object(config, 'YT_TOKEN_URL', self.srv.base + '/ytoauth/token'),
        ]
        for p in self._patches:
            p.start()
        net._SECRETS.clear()
        return self

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()
        self.srv.stop()

    # -- yardımcılar -----------------------------------------------------------
    def store(self, directory=None):
        from ops.store import Store
        return Store('file://' + self.remote, directory)

    def log(self):
        return [f'{c.method} {c.path}' for c in self.srv.calls]
