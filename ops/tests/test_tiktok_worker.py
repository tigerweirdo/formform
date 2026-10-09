"""TikTok bağlanma yolları (Cloudflare Worker, dashboard/clock.js): /auth/tiktok/start ve /auth/tiktok/callback.

Gerçek Node ile çalışır (sahte Request/Response). Asıl güvence: yalnız sahibi bağlanabilir. Anahtarsız, yanlış
anahtarlı, çerezsiz ya da state'i uyuşmayan hiçbir istek GitHub'a yetkilendirme kodu ilettirmez."""
import json
import os
import re
import shutil
import subprocess
import unittest
from urllib.parse import parse_qs, urlsplit

from ops import config
from ops.tests.test_workflows_yaml import load, triggers

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SCRIPT = r"""
import worker, { tiktokCallback, dispatchTiktokConnect, constantTimeEqual, TIKTOK_SCOPES } from './dashboard/clock.js';
const ORIGIN = %(origin)s;
const env = { TIKTOK_CLIENT_KEY: 'awfakeclientkey0123', TIKTOK_CONNECT_KEY: 'sesame-key-123', GITHUB_DISPATCH_TOKEN: 'ghp_secret',
              ASSETS: { fetch: async () => new Response('asset') } };
const post = (key) => new Request(ORIGIN + '/auth/tiktok/start', { method: 'POST',
  headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: 'key=' + encodeURIComponent(key) });
async function snap(res) { return { status: res.status, headers: Object.fromEntries(res.headers), body: await res.text() }; }
const out = {};
out.getStart = await snap(await worker.fetch(new Request(ORIGIN + '/auth/tiktok/start'), env));
out.wrongKey = await snap(await worker.fetch(post('nope'), env));
out.emptyKey = await snap(await worker.fetch(post(''), env));
out.rightKey = await snap(await worker.fetch(post('sesame-key-123'), env));
out.notConfigured = await snap(await worker.fetch(new Request(ORIGIN + '/auth/tiktok/start'), { ASSETS: env.ASSETS }));
out.putStart = await snap(await worker.fetch(new Request(ORIGIN + '/auth/tiktok/start', { method: 'PUT' }), env));
const state = new URL(out.rightKey.headers.location).searchParams.get('state');
const calls = [];
const dispatch = async (e, code) => { calls.push(code); return true; };
const cb = (qs, cookie) => new Request(ORIGIN + '/auth/tiktok/callback?' + qs, { headers: cookie ? { Cookie: cookie } : {} });
const viaRoute = []; const realFetch0 = globalThis.fetch;
globalThis.fetch = async (url, init) => { viaRoute.push({ url: String(url), body: init.body }); return new Response(null, { status: 204 }); };
out.okCallback = await snap(await worker.fetch(cb('code=THECODE123&state=' + state, 'tt_state=' + state), env));
globalThis.fetch = realFetch0;
out.viaRoute = viaRoute;
out.okViaHandler = await snap(await tiktokCallback(cb('code=THECODE456&state=' + state, 'tt_state=' + state), env, dispatch));
out.noCookie = await snap(await tiktokCallback(cb('code=X&state=' + state), env, dispatch));
out.badState = await snap(await tiktokCallback(cb('code=X&state=other', 'tt_state=' + state), env, dispatch));
out.noState = await snap(await tiktokCallback(cb('code=X', 'tt_state=' + state), env, dispatch));
out.unconfigured = await snap(await tiktokCallback(cb('code=X&state=' + state, 'tt_state=' + state), { ASSETS: env.ASSETS }, dispatch));
out.denied = await snap(await tiktokCallback(cb('error=access_denied&error_description=%%3Cscript%%3Ealert(1)%%3C%%2Fscript%%3E&state=' + state,
  'tt_state=' + state), env, dispatch));
out.noCode = await snap(await tiktokCallback(cb('state=' + state, 'tt_state=' + state), env, dispatch));
out.failedDispatch = await snap(await tiktokCallback(cb('code=Y&state=' + state, 'tt_state=' + state), env, async () => false));
out.throwingDispatch = await snap(await tiktokCallback(cb('code=Y&state=' + state, 'tt_state=' + state), env, async () => { throw new Error('boom'); }));
out.calls = calls;
out.asset = await snap(await worker.fetch(new Request(ORIGIN + '/about.html'), env));
const seen = []; const real = globalThis.fetch;
globalThis.fetch = async (url, init) => { seen.push({ url: String(url), method: init.method, auth: init.headers.Authorization, body: init.body });
  return new Response(null, { status: 204 }); };
out.dispatchOk = await dispatchTiktokConnect(env, 'CODE9');
out.seen = seen;
globalThis.fetch = async () => new Response(null, { status: 422 });
try { await dispatchTiktokConnect(env, 'CODE9'); out.dispatchRejected = 'no error'; }
catch (e) { out.dispatchRejected = { reason: e.reason, status: e.status }; }
try { await dispatchTiktokConnect({ ASSETS: env.ASSETS }, 'CODE9'); out.dispatchNoToken = 'no error'; }
catch (e) { out.dispatchNoToken = { reason: e.reason }; }
// Worker'da GitHub anahtarı yok: kullanıcıya nedeni söylenir ve GitHub'a hiç istek gitmez
const noTokenCalls = [];
globalThis.fetch = async (url) => { noTokenCalls.push(String(url)); return new Response(null, { status: 204 }); };
const envNoToken = { TIKTOK_CLIENT_KEY: env.TIKTOK_CLIENT_KEY, TIKTOK_CONNECT_KEY: env.TIKTOK_CONNECT_KEY, ASSETS: env.ASSETS };
out.noTokenPage = await snap(await worker.fetch(cb('code=NOTOKENCODE&state=' + state, 'tt_state=' + state), envNoToken));
out.noTokenCalls = noTokenCalls;
// GitHub anahtarı reddeder (401): durum kodu sayfada görünür, kod ve anahtar görünmez
globalThis.fetch = async () => new Response(null, { status: 401 });
out.githubRefuses = await snap(await worker.fetch(cb('code=REFUSEDCODE&state=' + state, 'tt_state=' + state), env));
globalThis.fetch = real;
out.eq = [constantTimeEqual('a', 'a'), constantTimeEqual('a', 'b'), constantTimeEqual('a', 'ab'), constantTimeEqual('', ''),
          constantTimeEqual(undefined, ''), constantTimeEqual('abc', 'abd')];
out.scopes = TIKTOK_SCOPES;
console.log(JSON.stringify(out));
"""


def run_worker():
    script = SCRIPT % {'origin': json.dumps(config.SITE_ORIGIN)}
    p = subprocess.run(['node', '--input-type=module', '-e', script], cwd=ROOT, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip() or 'node hatası')
    return json.loads(p.stdout)


@unittest.skipUnless(shutil.which('node'), 'node yok')
class TiktokWorker(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = run_worker()

    def test_the_key_page_asks_for_the_owner_key_and_redirects_nowhere(self):
        page = self.out['getStart']
        self.assertEqual(page['status'], 200)
        self.assertIn('name="key"', page['body'])
        self.assertIn('method="post"', page['body'])                         # anahtar adres satırında görünmez
        self.assertNotIn('location', page['headers'])
        self.assertNotIn('set-cookie', page['headers'])
        self.assertIn('restricted to the owner', page['body'])
        for scope in config.TIKTOK_SCOPES.split(','):                        # yetkiler sayfada açıkça yazar
            self.assertIn(scope, page['body'])

    def test_a_wrong_or_empty_key_starts_nothing(self):
        for name in ('wrongKey', 'emptyKey'):
            r = self.out[name]
            self.assertEqual(r['status'], 401, name)
            self.assertNotIn('location', r['headers'], name)
            self.assertNotIn('set-cookie', r['headers'], name)

    def test_the_right_key_redirects_to_tiktok_with_the_documented_parameters_and_a_state_cookie(self):
        r = self.out['rightKey']
        self.assertEqual(r['status'], 303)
        loc = urlsplit(r['headers']['location'])
        self.assertEqual(f'{loc.scheme}://{loc.netloc}{loc.path}', 'https://www.tiktok.com/v2/auth/authorize/')
        q = {k: v[0] for k, v in parse_qs(loc.query).items()}
        self.assertEqual(q['client_key'], 'awfakeclientkey0123')
        self.assertEqual(q['response_type'], 'code')
        self.assertEqual(q['scope'], config.TIKTOK_SCOPES)                   # Python ve Worker aynı yetkileri ister
        self.assertEqual(q['redirect_uri'], config.TIKTOK_REDIRECT_URI)      # token değişimindeki adresle aynı olmak zorunda
        self.assertRegex(q['state'], r'^[0-9a-f]{32}$')
        self.assertEqual(q['disable_auto_auth'], '1')                        # izin ekranı her seferinde gösterilir
        self.assertNotIn('video.publish', q['scope'])
        cookie = r['headers']['set-cookie']
        for part in (f"tt_state={q['state']}", 'HttpOnly', 'Secure', 'SameSite=Lax', 'Path=/auth/tiktok/', 'Max-Age=600'):
            self.assertIn(part, cookie)
        self.assertNotIn('sesame-key-123', json.dumps(r))                    # anahtar yanıtta geri yansımaz

    def test_each_start_gets_its_own_unpredictable_state(self):
        # iki ayrı çalıştırma farklı state üretir
        states = {parse_qs(urlsplit(run_worker()['rightKey']['headers']['location']).query)['state'][0] for _ in range(2)}
        self.assertEqual(len(states), 2)

    def test_it_is_off_unless_both_keys_are_configured_and_only_get_and_post_are_allowed(self):
        self.assertEqual(self.out['notConfigured']['status'], 404)
        self.assertEqual(self.out['putStart']['status'], 405)
        self.assertEqual(self.out['putStart']['headers']['allow'], 'GET, POST')

    def test_a_valid_callback_forwards_the_code_once_and_never_shows_it(self):
        for name in ('okCallback', 'okViaHandler'):
            r = self.out[name]
            self.assertEqual(r['status'], 200, name)
            self.assertIn('Authorization received', r['body'], name)
            self.assertNotIn('THECODE', r['body'], name)                     # kod ekrana basılmaz
            self.assertIn('Max-Age=0', r['headers']['set-cookie'], name)     # çerez tek kullanımlık
        # /auth/tiktok/callback yolu gerçek iletim fonksiyonuyla çalışır ve kodu yalnız GitHub'a, yalnız bir kez iletir
        [call] = self.out['viaRoute']
        self.assertTrue(call['url'].endswith('/actions/workflows/tiktok.yml/dispatches'))
        self.assertEqual(json.loads(call['body'])['inputs'], {'action': 'connect', 'code': 'THECODE123'})

    def test_nothing_is_forwarded_unless_the_flow_started_here(self):
        for name, status in (('noCookie', 400), ('badState', 400), ('noState', 400), ('unconfigured', 400)):
            self.assertEqual(self.out[name]['status'], status, name)
            self.assertIn('not started here', self.out[name]['body'], name)
        # Tüm olumsuz yollar bittikten sonra GitHub'a iletilen tek kod, geçerli akışınki
        self.assertEqual(self.out['calls'], ['THECODE456'])

    def test_a_denied_authorization_is_shown_escaped_and_forwards_nothing(self):
        r = self.out['denied']
        self.assertEqual(r['status'], 200)
        self.assertIn('did not authorize', r['body'])
        self.assertIn('&lt;script&gt;', r['body'])
        self.assertNotIn('<script>', r['body'])
        self.assertEqual(self.out['noCode']['status'], 400)

    def test_a_failed_dispatch_is_reported_not_hidden(self):
        for name in ('failedDispatch', 'throwingDispatch'):
            self.assertEqual(self.out[name]['status'], 502, name)
            self.assertIn('could not be finished', self.out[name]['body'], name)

    def test_a_missing_github_key_is_named_and_nothing_is_sent_anywhere(self):
        r = self.out['noTokenPage']
        self.assertEqual(r['status'], 503)
        self.assertIn('setup is incomplete', r['body'])
        self.assertEqual(self.out['noTokenCalls'], [])
        self.assertNotIn('NOTOKENCODE', r['body'])

    def test_a_github_refusal_shows_the_status_but_never_the_code_or_key(self):
        r = self.out['githubRefuses']
        self.assertEqual(r['status'], 502)
        self.assertIn('GitHub answered with status 401', r['body'])
        for secret in ('REFUSEDCODE', 'ghp_secret', 'sesame-key-123'):
            self.assertNotIn(secret, r['body'])

    def test_every_page_has_strict_headers_and_links_the_policies(self):
        for name in ('getStart', 'rightKey', 'okCallback', 'badState', 'denied'):
            h = self.out[name]['headers']
            self.assertEqual(h['cache-control'], 'no-store', name)
            self.assertEqual(h['referrer-policy'], 'no-referrer', name)
        for name in ('getStart', 'okCallback', 'denied'):
            h, body = self.out[name]['headers'], self.out[name]['body']
            self.assertIn("default-src 'none'", h['content-security-policy'], name)
            self.assertNotIn('<script', body, name)
            for page in ('/about.html', '/privacy.html', '/terms.html'):
                self.assertIn(f'href="{page}"', body, name)

    def test_other_paths_still_go_to_the_static_assets(self):
        self.assertEqual(self.out['asset']['body'], 'asset')

    def test_dispatch_goes_only_to_the_tiktok_workflow_with_exactly_the_documented_inputs(self):
        self.assertTrue(self.out['dispatchOk'])
        self.assertEqual(self.out['dispatchRejected'], {'reason': 'github', 'status': 422})
        self.assertEqual(self.out['dispatchNoToken'], {'reason': 'no-token'})
        [call] = self.out['seen']
        owner, repo = config.REPO.split('/', 1)
        self.assertEqual(call['url'], f'https://api.github.com/repos/{owner}/{repo}/actions/workflows/tiktok.yml/dispatches')
        self.assertEqual((call['method'], call['auth']), ('POST', 'Bearer ghp_secret'))
        self.assertEqual(json.loads(call['body']), {'ref': 'main', 'inputs': {'action': 'connect', 'code': 'CODE9'}})
        # Worker'ın gönderdiği girdiler workflow'da gerçekten var
        inputs = triggers(load('tiktok')[0])['workflow_dispatch']['inputs']
        self.assertIn('connect', inputs['action']['options'])
        self.assertIn('code', inputs)

    def test_constant_time_comparison_is_correct(self):
        self.assertEqual(self.out['eq'], [True, False, False, True, True, False])

    def test_the_worker_origin_matches_the_published_panel_address(self):
        with open(os.path.join(ROOT, 'dashboard', 'README.md'), encoding='utf-8') as f:
            self.assertIn('SITE_ORIGIN', f.read())
        self.assertRegex(config.SITE_ORIGIN, r'^https://[a-z0-9.-]+\.workers\.dev$')
        self.assertEqual(config.TIKTOK_REDIRECT_URI, config.SITE_ORIGIN + '/auth/tiktok/callback')
        self.assertLess(len(config.TIKTOK_REDIRECT_URI), 512)               # TikTok sınırı


if __name__ == '__main__':
    unittest.main()
