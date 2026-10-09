"""Çekirdek parçalar: şifreleme, veri deposu, HTTP istemcisi."""
import json
import os
import subprocess
import tempfile
import threading
import unittest

from ops import net, seal
from ops.errors import StoreError
from ops.store import DNA_COLUMNS, VIDEO_COLUMNS, Store
from ops.tests.fakes import FakeServer


def bare_remote():
    d = tempfile.mkdtemp(prefix='ff-remote-')
    path = os.path.join(d, 'r.git')
    subprocess.run(['git', 'init', '-q', '--bare', path], check=True)
    return path


def count(remote):
    out = subprocess.run(['git', '-C', remote, 'rev-list', '--count', 'veri'], capture_output=True, text=True)
    return int(out.stdout or 0)


class Seal(unittest.TestCase):
    TOKEN = 'IGAA' + 'abcDEF123' * 8

    def test_roundtrip(self):
        for text in ('', 'kısa', self.TOKEN, 'türkçe ığüşöç ✓ ' * 40):
            self.assertEqual(seal.unseal('anahtar', seal.seal('anahtar', text)), text)

    def test_wrong_key_or_tampering_gives_none(self):
        blob = seal.seal('anahtar', self.TOKEN)
        self.assertIsNone(seal.unseal('baska', blob))
        raw = bytearray(__import__('base64').urlsafe_b64decode(blob))
        for pos in (3, 10, 25, len(raw) - 1):
            bad = bytearray(raw)
            bad[pos] ^= 1
            self.assertIsNone(seal.unseal('anahtar', __import__('base64').urlsafe_b64encode(bytes(bad)).decode()))
        for junk in ('', 'x', 'bozuk!!', blob[:20]):
            self.assertIsNone(seal.unseal('anahtar', junk))

    def test_ciphertext_hides_plaintext_and_is_randomised(self):
        a, b = seal.seal('k', self.TOKEN), seal.seal('k', self.TOKEN)
        self.assertNotEqual(a, b)
        self.assertNotIn('IGAA', a)
        self.assertNotIn(self.TOKEN[4:20], __import__('base64').urlsafe_b64decode(a).decode('latin-1'))


class StoreTests(unittest.TestCase):
    def test_csv_roundtrip_with_hard_text(self):
        remote = bare_remote()
        caption = 'Satır 1, "tırnaklı"\n\nİkinci satır; ğüşiöç ✓\n#formform'
        row = {c: '' for c in VIDEO_COLUMNS}
        row.update(video_id='FF_1', caption=caption, materials='a, b, c', views_72h=1234)
        Store('file://' + remote).transaction(lambda d: d.videos.append(row), 'ilk')
        back = Store('file://' + remote).read().videos
        self.assertEqual(len(back), 1)
        self.assertEqual(back[0]['caption'], caption)
        self.assertEqual(back[0]['materials'], 'a, b, c')
        self.assertEqual(back[0]['views_72h'], '1234')

    def test_creates_orphan_data_branch_with_expected_files(self):
        remote = bare_remote()
        Store('file://' + remote).transaction(lambda d: None, 'başlangıç')
        files = subprocess.run(['git', '-C', remote, 'ls-tree', '--name-only', 'veri'], capture_output=True,
                               text=True).stdout.split()
        self.assertEqual(sorted(files), ['README.md', 'config.json', 'dna_weights.csv', 'videos.csv'])
        head = subprocess.run(['git', '-C', remote, 'show', 'veri:videos.csv'], capture_output=True, text=True).stdout
        self.assertEqual(head.strip(), ','.join(VIDEO_COLUMNS))
        self.assertEqual(subprocess.run(['git', '-C', remote, 'show', 'veri:dna_weights.csv'], capture_output=True,
                                        text=True).stdout.strip(), ','.join(DNA_COLUMNS))
        main_has = subprocess.run(['git', '-C', remote, 'rev-parse', '--verify', '-q', 'main'], capture_output=True)
        self.assertNotEqual(main_has.returncode, 0)                    # `main` dalına dokunulmaz

    def test_no_commit_when_nothing_changed(self):
        remote = bare_remote()
        s = Store('file://' + remote)
        s.transaction(lambda d: d.videos.append({'video_id': 'A'}), 'bir')
        before = count(remote)
        s.transaction(lambda d: None, 'boş')
        s.transaction(lambda d: d.videos[0].update(video_id='A'), 'aynı değer')
        self.assertEqual(count(remote), before)

    def test_unknown_columns_survive_and_missing_columns_default_blank(self):
        remote = bare_remote()
        s = Store('file://' + remote)
        s.transaction(lambda d: d.videos.append({'video_id': 'A', 'eski_sutun': 'x'}), 'bir')
        data = Store('file://' + remote).read()
        self.assertEqual(data.videos[0]['eski_sutun'], 'x')
        self.assertEqual(data.videos[0]['likes'], '')
        s.transaction(lambda d: d.videos[0].update(likes=5), 'iki')
        self.assertEqual(Store('file://' + remote).read().videos[0]['eski_sutun'], 'x')

    def test_concurrent_writers_lose_nothing(self):
        remote = bare_remote()
        errors = []

        def worker(i):
            try:
                st = Store('file://' + remote)
                for j in range(3):
                    vid = f'W{i}_{j}'
                    st.transaction(lambda d, vid=vid: None if any(r['video_id'] == vid for r in d.videos)
                                   else d.videos.append({'video_id': vid}), vid)
            except Exception as e:                                     # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(errors, [])
        ids = sorted(r['video_id'] for r in Store('file://' + remote).read().videos)
        self.assertEqual(ids, sorted(f'W{i}_{j}' for i in range(6) for j in range(3)))

    def test_transaction_reapplies_function_on_fresh_data_after_conflict(self):
        remote = bare_remote()
        a, b = Store('file://' + remote), Store('file://' + remote)
        a.transaction(lambda d: d.videos.append({'video_id': 'X', 'likes': '1'}), 'x')
        seen = []

        def bump(d):
            seen.append(len(d.videos))
            d.videos[0]['likes'] = str(int(d.videos[0]['likes']) + 1)
            if len(seen) == 1:                         # ilk denemede başkası araya girer
                a.transaction(lambda dd: dd.videos.append({'video_id': 'Y'}), 'y')
        b.transaction(bump, 'bump')
        data = Store('file://' + remote).read()
        self.assertEqual([r['video_id'] for r in data.videos], ['X', 'Y'])
        self.assertEqual(data.videos[0]['likes'], '2')                 # iki kez artmadı, kaybolmadı
        self.assertEqual(seen, [1, 2])

    def test_fails_loudly_when_remote_unreachable(self):
        s = Store('file:///yok/boyle/bir/depo.git', attempts=2)
        with self.assertRaises(StoreError):
            s.transaction(lambda d: d.videos.append({'video_id': 'A'}), 'x')

    def test_sealed_token_and_config_persist(self):
        remote = bare_remote()

        def save(d):
            d.sealed_token = 'ABC'
            d.config['ig_token_expires_at'] = '2026-12-01T00:00:00Z'
        Store('file://' + remote).transaction(save, 'jeton')
        data = Store('file://' + remote).read()
        self.assertEqual((data.sealed_token, data.config['ig_token_expires_at']), ('ABC', '2026-12-01T00:00:00Z'))


class NetTests(unittest.TestCase):
    def setUp(self):
        self.srv = FakeServer()
        self.addCleanup(self.srv.stop)
        net._SECRETS.clear()

    def test_multipart_encoding(self):
        body, ctype = net.encode_multipart({'a': '1', 'b': 'ğ'}, {'file': ('v.mp4', b'\x00\x01bin', 'video/mp4')})
        boundary = ctype.split('boundary=')[1]
        self.assertTrue(body.endswith(f'--{boundary}--\r\n'.encode()))
        self.assertIn(b'name="a"\r\n\r\n1', body)
        self.assertIn('ğ'.encode(), body)
        self.assertIn(b'filename="v.mp4"', body)
        self.assertIn(b'\x00\x01bin', body)

    def test_retries_server_errors_only_when_asked(self):
        n = {'c': 0}

        def flaky(c, m):
            n['c'] += 1
            return (500, {'e': 1}) if n['c'] < 3 else (200, {'ok': True})
        self.srv.route('GET', '/x', flaky)
        with self.assertRaises(net.HttpError):
            net.request('GET', self.srv.base + '/x')
        self.assertEqual(n['c'], 1)                                    # varsayılan: tekrar yok
        n['c'] = 0
        self.assertEqual(net.request('GET', self.srv.base + '/x', retries=2, backoff=0), {'ok': True})
        self.assertEqual(n['c'], 3)

    def test_client_errors_are_never_retried(self):
        self.srv.route('GET', '/y', lambda c, m: (404, {'e': 1}))
        with self.assertRaises(net.HttpError) as cm:
            net.request('GET', self.srv.base + '/y', retries=3, backoff=0)
        self.assertEqual(cm.exception.status, 404)
        self.assertEqual(len(self.srv.named('GET', '/y')), 1)

    def test_post_form_and_json_bodies(self):
        self.srv.route('POST', '/p', lambda c, m: (200, {'ct': c.headers['Content-Type'], 'body': c.body.decode()}))
        r = net.request('POST', self.srv.base + '/p', form={'a': 'b c', 'd': 'ç'})
        self.assertEqual(r['ct'], 'application/x-www-form-urlencoded')
        self.assertEqual(r['body'], 'a=b+c&d=%C3%A7')
        r = net.request('POST', self.srv.base + '/p', json_body={'k': [1, 2]})
        self.assertEqual((r['ct'], json.loads(r['body'])), ('application/json', {'k': [1, 2]}))

    def test_errors_never_contain_registered_secrets_or_query_strings(self):
        net.add_secret('SUPER-GIZLI-DEGER-123')
        self.srv.route('GET', '/z', lambda c, m: (400, {'echo': c.query.get('access_token')}))
        with self.assertRaises(net.HttpError) as cm:
            net.request('GET', self.srv.base + '/z', query={'access_token': 'SUPER-GIZLI-DEGER-123'})
        self.assertNotIn('SUPER-GIZLI-DEGER-123', str(cm.exception))
        self.assertNotIn('access_token', str(cm.exception).split(':', 2)[1])      # adresin sorgu kısmı yok
        self.assertEqual(net.scrub('a SUPER-GIZLI-DEGER-123 b'), 'a *** b')

    def test_connection_refused_is_wrapped(self):
        with self.assertRaises(net.HttpError) as cm:
            net.request('GET', 'http://127.0.0.1:1/yok', timeout=2)
        self.assertEqual(cm.exception.status, 0)

    def test_download_follows_redirect_without_credentials_and_is_atomic(self):
        self.srv.route('GET', '/dl', lambda c, m: (302, b'', {'Location': '/file'}))
        self.srv.route('GET', '/file', lambda c, m: (200, b'ICERIK' * 1000))
        with tempfile.TemporaryDirectory() as d:
            dest = os.path.join(d, 'v.mp4')
            net.download(self.srv.base + '/dl', dest, headers={'Authorization': 'Bearer gizli'})
            with open(dest, 'rb') as f:
                self.assertEqual(f.read(), b'ICERIK' * 1000)
            self.assertEqual(os.listdir(d), ['v.mp4'])                 # yarım dosya (.part) kalmadı
        self.assertNotIn('Authorization', self.srv.named('GET', '/file')[0].headers)
        self.assertEqual(self.srv.named('GET', '/dl')[0].headers['Authorization'], 'Bearer gizli')

    def test_download_failure_leaves_no_file(self):
        self.srv.route('GET', '/gone', lambda c, m: (404, {'e': 1}))
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(net.HttpError):
                net.download(self.srv.base + '/gone', os.path.join(d, 'v.mp4'))
            self.assertEqual(os.listdir(d), [])


if __name__ == '__main__':
    unittest.main()
