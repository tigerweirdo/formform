"""YouTube yayın akışı: sahte GitHub + Google + YouTube üzerinde uçtan uca.

Instagram akışıyla aynı güvenceler: üstlenme yüklemeden önce, oturum açma ve dosya gönderme asla yeniden denenmez,
hatalı yayında satır 'claimed' kalır, aynı video iki kez yüklenmez. Ek olarak: YouTube, Instagram'dan bağımsızdır
(biri hata verince ötekinin durumu bozulmaz, release adına dokunulmaz).
"""
import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

from ops import ledger, publish, youtube
from ops.errors import (CredentialsMissing, NotReady, OpsError, StoreError, YoutubeAuthFailed, YoutubeFailed,
                        YoutubeUncertain)
from ops.tests.fakes import (VIDEO_BYTES, YT_ACCESS, YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH, World, release)

CLOCK = lambda: datetime(2026, 10, 2, 5, 0, 7, 123000, tzinfo=timezone.utc)   # noqa: E731
START = r'/yt/upload/youtube/v3/videos'
PUT = r'/yt/upload/session/S1'


def run(world, **kw):
    return publish.run(world.store(), platform='youtube', clock=CLOCK, log=lambda *_: None,
                       workdir=tempfile.mkdtemp(), **kw)


def run_instagram(world):
    return publish.run(world.store(), clock=CLOCK, sleep=lambda s: None, log=lambda *_: None,
                       workdir=tempfile.mkdtemp())


def rows(world):
    return world.store().read().ledger('youtube').videos


def header(call, name):
    """urllib başlık adlarını 'X-upload-content-length' gibi yazar: büyük/küçük harfe bakılmaz."""
    return next((v for k, v in call.headers.items() if k.lower() == name.lower()), None)


def tree(remote):
    return subprocess.run(['git', '-C', remote, 'ls-tree', '-r', '--name-only', 'veri'], capture_output=True,
                          text=True).stdout.split()


class HappyPath(unittest.TestCase):
    def test_uploads_and_records_the_video(self):
        with World([release(1)]) as w:
            out = run(w)
            self.assertEqual(out, {'video_id': 'FF_20261002_060100', 'youtube_id': 'YTVID1'})
            log = w.log()
            order = [log.index(x) for x in ('POST /ytoauth/token', 'GET /gh/repos/o/r/releases', 'GET /blob/video.mp4',
                                            f'POST {START}', f'PUT {PUT}')]
            self.assertEqual(order, sorted(order), f'sıra bozuk: {log}')
            self.assertEqual([c for c in log if '/cld/' in c], [], 'doğrudan yükleme: Cloudinary gerekmez')

            [r] = rows(w)
            self.assertEqual((r['video_id'], r['status'], r['youtube_id'], r['published_at']),
                             ('FF_20261002_060100', 'published', 'YTVID1', '2026-10-02T05:00:07.123Z'))
            self.assertEqual((r['structure'], r['palette'], r['materials'], r['loop_strategy'], r['palette_weight']),
                             ('spiral form', 'violet-pink', 'a, b', 'seamless_8s', '1.2'))
            self.assertEqual(w.store().read().videos, [])                       # Instagram kaydına dokunulmadı
            self.assertTrue(w.releases[501]['name'].startswith('render '))      # release adı Instagram'ındır, değişmedi

    def test_sends_oauth_form_metadata_and_the_file(self):
        with World([release(1)]) as w:
            run(w)
            form = w.srv.named('POST', r'/ytoauth/token')[0].form()
            self.assertEqual(form, {'grant_type': 'refresh_token', 'client_id': YT_CLIENT_ID,
                                    'client_secret': YT_CLIENT_SECRET, 'refresh_token': YT_REFRESH})

            start = w.srv.named('POST', START)[0]
            self.assertEqual((start.query['uploadType'], start.query['part']), ('resumable', 'snippet,status'))
            self.assertEqual(header(start, 'Authorization'), f'Bearer {YT_ACCESS}')
            self.assertEqual(header(start, 'X-Upload-Content-Length'), str(len(VIDEO_BYTES)))
            self.assertEqual(header(start, 'X-Upload-Content-Type'), 'video/mp4')
            meta = start.json()
            self.assertEqual(meta['snippet']['title'], 'A temporary geometry.')
            self.assertTrue(meta['snippet']['description'].startswith('A temporary geometry.'))
            self.assertTrue(meta['snippet']['description'].endswith('#Shorts'))
            self.assertEqual(meta['snippet']['tags'], ['formform', 'abstractart'])
            self.assertEqual(meta['snippet']['categoryId'], '1')
            self.assertEqual(meta['status'], {'privacyStatus': 'public', 'selfDeclaredMadeForKids': False})

            put = w.srv.named('PUT', PUT)[0]
            self.assertEqual(put.body, VIDEO_BYTES)
            self.assertEqual(header(put, 'Content-Type'), 'video/mp4')
            self.assertEqual(header(put, 'Authorization'), f'Bearer {YT_ACCESS}')

    def test_privacy_is_configurable_and_validated_before_any_request(self):
        with World([release(1)], env={'YT_PRIVACY': 'Unlisted'}) as w:
            run(w)
            self.assertEqual(w.srv.named('POST', START)[0].json()['status']['privacyStatus'], 'unlisted')
        with World([release(1)], env={'YT_PRIVACY': 'herkes'}) as w:
            with self.assertRaises(OpsError) as cm:
                run(w)
            self.assertIn('YT_PRIVACY geçersiz', str(cm.exception))
            self.assertEqual(w.log(), [])

    def test_the_claim_is_on_record_before_the_upload_starts(self):
        with World([release(1)]) as w:
            seen = {}

            def spy(c, m):
                seen['rows'] = [(r['video_id'], r['status'], r['youtube_id'], r['published_at'])
                                for r in w.store().read().ledger('youtube').videos]
                return 200, {}, {'Location': w.srv.base + '/yt/upload/session/S1'}
            w.srv.route('POST', START, spy)
            run(w)
            self.assertEqual(seen['rows'], [('FF_20261002_060100', 'claimed', '', '')])

    def test_the_second_run_never_uploads_the_same_video_again(self):
        with World([release(1)]) as w:
            run(w)
            with self.assertRaises(NotReady):
                run(w)
            self.assertEqual(len(w.srv.named('POST', START)), 1)
            self.assertEqual(len(w.srv.named('PUT', PUT)), 1)
            self.assertEqual(len(rows(w)), 1)

    def test_picks_the_newest_render_and_leaves_older_ones_for_a_later_slot(self):
        rels = [release(1, created='2026-10-02T01:00:00Z'), release(2, created='2026-10-02T03:30:00Z')]
        with World(rels) as w:
            self.assertEqual(run(w)['video_id'], 'FF_20261002_060200')

    def test_a_stale_render_is_not_posted(self):
        with World([release(1, created='2026-09-30T03:30:00Z')]) as w:
            with self.assertRaises(NotReady) as cm:
                run(w)
            self.assertIn('24 saatten eski', str(cm.exception))
            self.assertEqual(w.srv.named('POST', START), [])


class IndependentOfInstagram(unittest.TestCase):
    def test_both_platforms_post_the_same_render_in_either_order(self):
        for order in (('instagram', 'youtube'), ('youtube', 'instagram')):
            with World([release(1)]) as w:
                for name in order:
                    (run_instagram if name == 'instagram' else run)(w)
                data = w.store().read()
                self.assertEqual([r['instagram_id'] for r in data.videos], ['MEDIA777'], order)
                self.assertEqual([r['youtube_id'] for r in data.ledger('youtube').videos], ['YTVID1'], order)
                self.assertEqual(w.releases[501]['name'], 'published FF_20261002_060100', order)   # Instagram'ın durumu
                with self.assertRaises(NotReady):                                                     # ikisi de bitti
                    run(w)
                with self.assertRaises(NotReady):
                    run_instagram(w)

    def test_a_youtube_failure_does_not_block_or_corrupt_instagram(self):
        with World([release(1)]) as w:
            w.yt_start_status = 500
            with self.assertRaises(YoutubeFailed):
                run(w)
            self.assertEqual(run_instagram(w)['instagram_id'], 'MEDIA777')
            self.assertEqual(w.releases[501]['name'], 'published FF_20261002_060100')
            self.assertEqual([r['status'] for r in rows(w)], ['claimed'])

    def test_an_instagram_failure_does_not_block_youtube(self):
        with World([release(1)]) as w:
            w.create_status = 500
            with self.assertRaises(Exception):
                run_instagram(w)
            self.assertEqual(w.releases[501]['name'], 'claimed FF_20261002_060100')     # Instagram kilitli kaldı
            self.assertEqual(run(w)['youtube_id'], 'YTVID1')                              # YouTube yine de paylaştı


class Failures(unittest.TestCase):
    def count(self, w, method, regex):
        return len(w.srv.named(method, regex))

    def test_missing_credentials_stop_before_any_request(self):
        with World([release(1)], env={'YT_REFRESH_TOKEN': ''}) as w:
            with self.assertRaises(CredentialsMissing) as cm:
                run(w)
            self.assertIn('YT_REFRESH_TOKEN', str(cm.exception))
            self.assertEqual(w.log(), [])
            self.assertEqual(tree(w.remote), [])

    def test_a_revoked_token_stops_before_anything_is_claimed(self):
        with World([release(1)]) as w:
            w.yt_token_status = 400
            with self.assertRaises(YoutubeAuthFailed) as cm:
                run(w)
            self.assertIn("'In production'", cm.exception.hint)
            self.assertEqual(self.count(w, 'POST', r'/ytoauth/token'), 1)           # 4xx yeniden denenmez
            self.assertEqual([c for c in w.log() if '/gh/' in c or '/yt/' in c], [])
            self.assertEqual(tree(w.remote), [])                                   # satır bile yazılmadı

    def test_no_ready_render_fails_without_side_effects(self):
        for rels in ([], [release(1, assets=False)]):
            with World(rels) as w:
                with self.assertRaises(NotReady) as cm:
                    run(w)
                self.assertIn('Yayınlanacak render yok (YouTube)', str(cm.exception))
                self.assertEqual(self.count(w, 'POST', START), 0)
                self.assertEqual(tree(w.remote), [])

    def test_a_failed_session_start_keeps_the_claim_and_is_not_retried(self):
        with World([release(1)]) as w:
            w.yt_start_status = 500
            with self.assertRaises(YoutubeFailed) as cm:
                run(w)
            self.assertIn('youtube/videos.csv', cm.exception.hint)
            self.assertEqual(self.count(w, 'POST', START), 1)                      # yeniden denenmedi
            self.assertEqual(self.count(w, 'PUT', PUT), 0)
            self.assertEqual([r['status'] for r in rows(w)], ['claimed'])
            with self.assertRaises(NotReady):                                      # sonraki tur da denemez
                run(w)
            self.assertEqual(self.count(w, 'POST', START), 1)

    def test_a_failed_upload_is_uncertain_never_retried_and_never_reposted(self):
        with World([release(1)]) as w:
            w.yt_upload_status = 500
            with self.assertRaises(YoutubeUncertain) as cm:
                run(w)
            self.assertNotIn('SECRETUPLOADID', str(cm.exception))                  # oturum adresi hata metninde yok
            self.assertEqual(self.count(w, 'PUT', PUT), 1)
            self.assertEqual([r['status'] for r in rows(w)], ['claimed'])
            w.yt_upload_status = 200
            with self.assertRaises(NotReady):                                      # YouTube'da video olabilir: tekrar yok
                run(w)
            self.assertEqual(self.count(w, 'PUT', PUT), 1)

    def test_unclaim_lets_a_failed_video_be_retried(self):
        with World([release(1)]) as w:
            w.yt_start_status = 500
            with self.assertRaises(YoutubeFailed):
                run(w)
            w.yt_start_status = 200
            self.assertTrue(ledger.unclaim(w.store(), 'youtube', 'FF_20261002_060100'))
            self.assertEqual(run(w)['youtube_id'], 'YTVID1')
            self.assertEqual(self.count(w, 'POST', START), 2)
            self.assertEqual([r['status'] for r in rows(w)], ['published'])

    def test_unclaim_never_touches_a_published_video(self):
        with World([release(1)]) as w:
            run(w)
            self.assertFalse(ledger.unclaim(w.store(), 'youtube', 'FF_20261002_060100'))
            self.assertEqual([(r['status'], r['youtube_id']) for r in rows(w)], [('published', 'YTVID1')])
            self.assertFalse(ledger.unclaim(w.store(), 'youtube', 'YOK'))
            with self.assertRaises(OpsError) as cm:
                ledger.unclaim(w.store(), 'instagram', 'FF_20261002_060100')
            self.assertIn('release adındadır', str(cm.exception))

    def test_losing_the_claim_race_stops_before_any_upload(self):
        with World([release(1)]) as w:
            with mock.patch('ops.publish.ledger.claim', return_value=False):
                with self.assertRaises(NotReady) as cm:
                    run(w)
            self.assertIn('zaten üstlenilmiş', str(cm.exception))
            self.assertEqual(self.count(w, 'POST', START), 0)

    def test_store_failure_after_upload_reports_the_youtube_id_and_keeps_the_claim(self):
        with World([release(1)]) as w:
            with mock.patch('ops.publish.ledger.settle', side_effect=StoreError('git push reddedildi')):
                with self.assertRaises(StoreError) as cm:
                    run(w)
            self.assertIn('YTVID1', str(cm.exception))
            self.assertIn('tekrar paylaşmayın', cm.exception.hint)
            self.assertEqual([r['status'] for r in rows(w)], ['claimed'])
            with self.assertRaises(NotReady):
                run(w)

    def test_secrets_never_appear_in_error_text(self):
        wrong = '1//0gWrongRefreshToken0123456789abcdef'
        with World([release(1)], env={'YT_REFRESH_TOKEN': wrong}) as w:
            with self.assertRaises(YoutubeAuthFailed) as cm:                       # sahte sunucu jetonu geri yansıtır
                run(w)
            text = str(cm.exception)
            for secret in (wrong, YT_CLIENT_SECRET, YT_REFRESH):
                self.assertNotIn(secret, text)


class DryRun(unittest.TestCase):
    def test_dry_run_checks_everything_and_changes_nothing(self):
        with World([release(1)]) as w:
            out = run(w, dry_run=True)
            self.assertEqual(out, {'dry_run': True, 'video_id': 'FF_20261002_060100'})
            log = w.log()
            for call in ('POST /ytoauth/token', 'GET /yt/youtube/v3/channels', 'GET /blob/video.mp4'):
                self.assertIn(call, log)
            self.assertEqual([c for c in log if c.startswith(('POST /yt/upload', 'PUT /yt'))], [])
            self.assertEqual([c for c in log if c.startswith('PATCH')], [])
            self.assertEqual(tree(w.remote), [])                                   # hiçbir şey üstlenilmedi
            self.assertTrue(w.releases[501]['name'].startswith('render '))


class Metadata(unittest.TestCase):
    def test_title_is_the_hook_trimmed_to_the_limit_and_cleaned(self):
        long_hook = 'Ağır ' * 40
        meta = youtube.metadata(f'{long_hook}\n\n#formform #a', 'public')
        self.assertLessEqual(len(meta['snippet']['title']), youtube.TITLE_MAX)
        self.assertTrue(meta['snippet']['title'].endswith('…'))
        self.assertNotIn('<', youtube.metadata('<b>Hook</b>\n\n#x', 'public')['snippet']['title'])
        self.assertNotIn('>', youtube.metadata('a > b\n\n#x', 'public')['snippet']['description'])

    def test_tags_start_with_the_brand_skip_duplicates_and_respect_the_budget(self):
        meta = youtube.metadata('Hook.\n\n#formform #art #art #study', 'private')
        self.assertEqual(meta['snippet']['tags'], ['formform', 'art', 'study'])
        many = ' '.join(f'#tag{i:03d}{"x" * 30}' for i in range(40))
        tags = youtube.metadata(f'Hook.\n\n{many}', 'public')['snippet']['tags']
        self.assertLessEqual(sum(len(t) + 1 for t in tags), youtube.TAGS_MAX_CHARS)
        self.assertEqual(youtube.metadata('Hook.', 'private')['status']['privacyStatus'], 'private')

    def test_an_empty_caption_still_gets_a_title(self):
        self.assertEqual(youtube.metadata('', 'public')['snippet']['title'], 'form/form')
        self.assertEqual(json.loads(json.dumps(youtube.metadata(None, 'public')))['snippet']['tags'], ['formform'])


if __name__ == '__main__':
    unittest.main()
