"""Yayın akışı: sahte GitHub + Instagram + Cloudinary üzerinde uçtan uca.

Asıl güvence çağrı sırası ve sayısıdır: üstlenme paylaşımdan önce, paylaşım/üstlenme/oluşturma asla yeniden denenmez,
hata halinde release 'claimed' kalır.
"""
import tempfile
import unittest
from datetime import datetime, timezone

from ops import cloudinary, config, publish
from ops.errors import NotReady, PublishUncertain, ReelFailed, StoreError, TokenMissing
from ops.tests.fakes import GH_TOKEN, TOKEN, World, release

CLOCK = lambda: datetime(2026, 10, 2, 5, 0, 7, 123000, tzinfo=timezone.utc)   # noqa: E731


def run(world, **kw):
    return publish.run(world.store(), clock=CLOCK, sleep=lambda s: None, log=lambda *_: None,
                       workdir=tempfile.mkdtemp(), **kw)


class PublishFlow(unittest.TestCase):
    def test_happy_path(self):
        with World([release(1)]) as w:
            out = run(w)
            self.assertEqual(out, {'video_id': 'FF_20261002_060100', 'instagram_id': 'MEDIA777'})
            log = w.log()
            cloud = config.CLOUD_NAME
            ig = config.IG_USER_ID
            order = [log.index(x) for x in (
                'PATCH /gh/repos/o/r/releases/501', 'GET /blob/video.mp4', f'POST /cld/{cloud}/video/upload',
                f'POST /ig/v23.0/{ig}/media', 'GET /ig/v23.0/CR1',
                f'POST /ig/v23.0/{ig}/media_publish')]
            self.assertEqual(order, sorted(order), f'sıra bozuk: {log}')
            self.assertEqual(w.releases[501]['name'], 'published FF_20261002_060100')
            self.assertIn(f'POST /cld/{config.CLOUD_NAME}/video/destroy', log)
            claim = w.srv.named('PATCH', r'/gh/repos/o/r/releases/501')[0]
            self.assertEqual(claim.json(), {'name': 'claimed FF_20261002_060100'})

            data = w.store().read()
            self.assertEqual(len(data.videos), 1)
            row = data.videos[0]
            self.assertEqual((row['video_id'], row['instagram_id'], row['loop_strategy']),
                             ('FF_20261002_060100', 'MEDIA777', 'seamless_8s'))
            self.assertEqual(row['published_at'], '2026-10-02T05:00:07.123Z')
            self.assertEqual(row['materials'], 'a, b')
            self.assertEqual(row['palette_weight'], '1.2')

    def test_sends_caption_video_url_and_auth(self):
        with World([release(1)]) as w:
            run(w)
            create = w.srv.named('POST', r'/ig/v23.0/\d+/media')[0]
            form = create.form()
            self.assertEqual(form['media_type'], 'REELS')
            self.assertEqual(form['share_to_feed'], 'true')
            self.assertTrue(form['video_url'].endswith('/cdn/up.mp4'))          # seamless: boomerang yok
            self.assertTrue(form['caption'].startswith('A temporary geometry.'))
            self.assertEqual(create.headers['Authorization'], f'Bearer {TOKEN}')
            gh = w.srv.named('GET', r'/gh/repos/o/r/releases')[0]
            self.assertEqual(gh.headers['Authorization'], f'Bearer {GH_TOKEN}')
            # Cloudinary yüklemesi imzalı: API anahtarı + imza alanları var
            up = w.srv.named('POST', r'/cld/[^/]+/video/upload')[0]
            self.assertIn(b'name="signature"', up.body)
            self.assertIn(b'name="api_key"', up.body)

    def test_forward_loop_uses_boomerang(self):
        r = release(1)
        import json
        body = json.loads(r['body'])
        body['loop'] = 'forward'
        r['body'] = json.dumps(body)
        self.assertEqual(
            publish.loop_url_for(body, 'https://res.cloudinary.com/x/video/upload/v1/a.mp4'),
            'https://res.cloudinary.com/x/video/upload/e_boomerang/v1/a.mp4')

    def test_asset_download_does_not_forward_authorization_to_redirect(self):
        with World([release(1)]) as w:
            run(w)
            blob = w.srv.named('GET', r'/blob/video.mp4')
            self.assertEqual(len(blob), 1)
            self.assertFalse(any(k.lower() == 'authorization' for k in blob[0].headers))
            first = w.srv.named('GET', r'/gh/repos/o/r/releases/assets/\d+')[0]
            self.assertEqual(first.headers['Accept'], 'application/octet-stream')

    def test_picks_newest_ready_and_skips_claimed_and_published(self):
        rels = [release(1, created='2026-10-01T03:30:00Z'), release(2, created='2026-10-02T03:30:00Z'),
                release(3, name='claimed FF_X', created='2026-10-03T03:30:00Z'),
                release(4, name='published FF_Y', created='2026-10-04T03:30:00Z')]
        with World(rels) as w:
            out = run(w)
            self.assertEqual(out['video_id'], 'FF_20261002_060200')
            self.assertEqual(w.releases[502]['name'], 'published FF_20261002_060200')
            self.assertTrue(w.releases[501]['name'].startswith('render '))      # eskisine dokunulmadı

    def test_same_release_is_never_published_twice(self):
        with World([release(1)]) as w:
            run(w)
            with self.assertRaises(NotReady):
                run(w)
            self.assertEqual(len(w.srv.named('POST', r'/ig/v23.0/\d+/media_publish')), 1)
            self.assertEqual(len(w.store().read().videos), 1)

    def test_no_ready_release_fails_without_side_effects(self):
        for rels in ([], [release(1, name='claimed X')], [release(1, assets=False)]):
            with World(rels) as w:
                with self.assertRaises(NotReady) as cm:
                    run(w)
                self.assertIn('Yayınlanacak render yok', str(cm.exception))
                self.assertEqual([c for c in w.log() if c.startswith(('PATCH', 'POST'))], [])

    def test_broken_release_is_skipped_for_next_one(self):
        bad = release(1, created='2026-10-03T03:30:00Z')
        bad['body'] = 'bozuk{'
        with World([bad, release(2, created='2026-10-02T03:30:00Z')]) as w:
            self.assertEqual(run(w)['video_id'], 'FF_20261002_060200')
            self.assertTrue(w.releases[501]['name'].startswith('render '))

    def test_missing_token_stops_before_any_request(self):
        with World([release(1)], env={'IG_ACCESS_TOKEN': ''}) as w:
            with self.assertRaises(TokenMissing):
                run(w)
            self.assertEqual(w.log(), [])

    def test_bearer_prefix_in_secret_is_tolerated(self):
        with World([release(1)], env={'IG_ACCESS_TOKEN': 'Bearer ' + TOKEN}) as w:
            run(w)
            create = w.srv.named('POST', r'/ig/v23.0/\d+/media')[0]
            self.assertEqual(create.headers['Authorization'], f'Bearer {TOKEN}')


class PublishFailures(unittest.TestCase):
    def assert_single(self, w, regex, method='POST'):
        self.assertEqual(len(w.srv.named(method, regex)), 1, 'yan etkili çağrı yeniden denendi: ' + str(w.log()))

    def test_claim_failure_stops_everything_and_is_not_retried(self):
        with World([release(1)]) as w:
            w.claim_status = 500
            with self.assertRaises(Exception):
                run(w)
            self.assert_single(w, r'/gh/repos/o/r/releases/501', 'PATCH')
            self.assertEqual([c for c in w.log() if '/ig/' in c or '/cld/' in c or 'blob' in c], [])

    def test_reel_error_keeps_release_claimed_and_does_not_publish(self):
        with World([release(1)]) as w:
            w.media_status = ['IN_PROGRESS', 'ERROR']
            with self.assertRaises(ReelFailed) as cm:
                run(w)
            self.assertIn('Instagram video işlenemedi: ERROR', str(cm.exception))
            # ERROR görüldüğü anda durur: bekleme zaman aşımına kadar sürmez
            self.assertEqual(len(w.srv.named('GET', r'/ig/v23.0/CR1')), 2)
            self.assertEqual(w.releases[501]['name'], 'claimed FF_20261002_060100')
            self.assertEqual(w.srv.named('POST', r'/ig/v23.0/\d+/media_publish'), [])
            self.assertEqual(w.store().read().videos, [])

    def test_reel_expired_also_fails(self):
        with World([release(1)]) as w:
            w.media_status = ['EXPIRED']
            with self.assertRaises(ReelFailed):
                run(w)

    def test_create_reel_failure_is_not_retried(self):
        with World([release(1)]) as w:
            w.create_status = 500
            with self.assertRaises(ReelFailed):
                run(w)
            self.assert_single(w, r'/ig/v23.0/\d+/media')
            self.assertEqual(w.releases[501]['name'], 'claimed FF_20261002_060100')

    def test_publish_failure_is_not_retried_and_is_reported_as_uncertain(self):
        with World([release(1)]) as w:
            w.publish_status = 500
            with self.assertRaises(PublishUncertain):
                run(w)
            self.assert_single(w, r'/ig/v23.0/\d+/media_publish')
            self.assertEqual(w.releases[501]['name'], 'claimed FF_20261002_060100')
            self.assertEqual(w.store().read().videos, [])

    def test_waits_until_finished(self):
        with World([release(1)]) as w:
            w.media_status = ['IN_PROGRESS', 'IN_PROGRESS', 'IN_PROGRESS', 'FINISHED']
            sleeps = []
            publish.run(w.store(), clock=CLOCK, sleep=sleeps.append, log=lambda *_: None, workdir=tempfile.mkdtemp())
            self.assertEqual(sleeps, [20, 20, 20, 20])
            self.assertEqual(len(w.srv.named('GET', r'/ig/v23.0/CR1')), 4)

    def test_gives_up_after_max_wait(self):
        with World([release(1)]) as w:
            w.media_status = ['IN_PROGRESS']
            with self.assertRaises(ReelFailed) as cm:
                run(w)
            self.assertIn('15 dakikada', str(cm.exception))
            self.assertEqual(w.srv.named('POST', r'/ig/v23.0/\d+/media_publish'), [])

    def test_store_failure_after_publish_reports_instagram_id_and_still_marks_published(self):
        with World([release(1)]) as w:
            store = w.store()

            def boom(fn, message):
                raise StoreError('git push reddedildi')
            store.transaction = boom
            with self.assertRaises(StoreError) as cm:
                publish.run(store, clock=CLOCK, sleep=lambda s: None, log=lambda *_: None, workdir=tempfile.mkdtemp())
            self.assertIn('MEDIA777', str(cm.exception))
            self.assertIn('tekrar paylaşmayın', cm.exception.hint)
            self.assertEqual(w.releases[501]['name'], 'published FF_20261002_060100')

    def test_rename_published_failure_does_not_fail_the_run(self):
        with World([release(1)]) as w:
            calls = {'n': 0}

            def flaky(c, m):
                calls['n'] += 1
                if calls['n'] == 2:                          # ikinci PATCH = published işareti
                    return 500, {'message': 'x'}
                w.releases[int(m.group(1))]['name'] = c.json()['name']
                return 200, {}
            w.srv.route('PATCH', r'/gh/repos/o/r/releases/(\d+)', flaky)
            out = run(w)
            self.assertEqual(out['instagram_id'], 'MEDIA777')
            self.assertEqual(len(w.store().read().videos), 1)


class DryRun(unittest.TestCase):
    def test_dry_run_checks_everything_and_changes_nothing(self):
        with World([release(1)]) as w:
            out = run(w, dry_run=True)
            self.assertTrue(out['dry_run'])
            log = w.log()
            self.assertIn('GET /ig/v23.0/me', log)
            self.assertIn(f'POST /cld/{config.CLOUD_NAME}/video/upload', log)
            self.assertIn(f'POST /cld/{config.CLOUD_NAME}/video/destroy', log)
            self.assertEqual([c for c in log if c.startswith('PATCH')], [])
            self.assertEqual(w.srv.named('POST', r'/ig/v23.0/\d+/media'), [])
            self.assertEqual(w.srv.named('POST', r'/ig/v23.0/\d+/media_publish'), [])
            self.assertTrue(w.releases[501]['name'].startswith('render '))
            self.assertEqual(w.store().read().videos, [])


class ClientDetails(unittest.TestCase):
    def test_cloudinary_signature_matches_documented_example(self):
        self.assertEqual(
            cloudinary.sign({'eager': 'w_400,h_300,c_pad|w_260,h_200,c_crop', 'public_id': 'sample_image',
                             'timestamp': '1315060510'}, 'abcd'),
            'bfd09f95f331f558cbd1320e67aa8d488770583e')

    def test_secrets_never_appear_in_error_text(self):
        with World([release(1)]) as w:
            w.srv.route('GET', r'/ig/refresh_access_token', lambda c, m: (400, {'echo': c.query['access_token']}))
            from ops import tokens
            with self.assertRaises(Exception) as cm:
                tokens.refresh(w.store())
            self.assertNotIn(TOKEN, str(cm.exception))
            self.assertNotIn(GH_TOKEN, str(cm.exception))


if __name__ == '__main__':
    unittest.main()
