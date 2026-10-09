"""TikTok: bağlanma (Login Kit), jeton yönetimi, taslak yükleme (Content Posting API Upload) ve istatistik.

Sahte TikTok sunucusu gerçek davranışı taklit eder: yetkilendirme kodu tek kullanımlıktır, yenileme jetonu
değişebilir, yükleme adresinde izin anahtarı vardır. Asıl güvenceler: jetonlar depoda düz metin olmaz, başka hesap
sessizce bağlı olanın yerine geçemez, üstlenme yüklemeden önce yapılır, yan etkili çağrılar yeniden denenmez."""
import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from ops import cli, config, ledger, publish, tiktok
from ops.errors import (CredentialsMissing, NotReady, OpsError, StoreError, TiktokAuthFailed, TiktokFailed,
                        TiktokNotConnected, TiktokUncertain)
from ops.tests.fakes import TT_CLIENT_KEY, TT_CLIENT_SECRET, TT_CODE, VIDEO_BYTES, World, release

CLOCK = lambda: datetime(2026, 10, 2, 5, 0, 7, 123000, tzinfo=timezone.utc)   # noqa: E731
NOW = CLOCK()
INIT = r'/tt/v2/post/publish/inbox/video/init/'
PUT = r'/tt-upload/abc'
STATUS = r'/tt/v2/post/publish/status/fetch/'
TOKEN = r'/tt/v2/oauth/token/'


def connect(w, replace=False, now=NOW):
    return tiktok.connect(w.store(), TT_CODE if not w._tt_used_codes else f'fakeauthcode.second{len(w._tt_used_codes)}',
                          now, replace=replace, log=lambda *_: None)


def run(w, **kw):
    sleeps = kw.pop('sleeps', [])
    return publish.run(w.store(), platform='tiktok', clock=CLOCK, sleep=sleeps.append, log=lambda *_: None,
                       workdir=tempfile.mkdtemp(), **kw)


def rows(w):
    return w.store().read().ledger('tiktok').videos


def veri_files(remote):
    names = subprocess.run(['git', '-C', remote, 'ls-tree', '-r', '--name-only', 'veri'], capture_output=True,
                           text=True).stdout.split()
    return {n: subprocess.run(['git', '-C', remote, 'show', f'veri:{n}'], capture_output=True, text=True).stdout
            for n in names}


def header(call, name):
    return next((v for k, v in call.headers.items() if k.lower() == name.lower()), None)


class Connect(unittest.TestCase):
    def test_exchanges_the_code_and_stores_the_tokens_encrypted(self):
        with World() as w:
            out = connect(w)
            self.assertEqual((out['display_name'], out['open_id']), ('formform.studio', 'OPENID_OWNER'))
            self.assertEqual(out['scope'], 'user.info.basic,video.upload,video.list')
            files = veri_files(w.remote)
            self.assertIn('tiktok_token.sealed', files)
            access, refresh = w._tt_valid.copy().pop(), w._tt_refresh
            for name, text in files.items():                                    # düz metin jeton hiçbir dosyada yok
                self.assertNotIn(access, text, name)
                self.assertNotIn(refresh, text, name)
                self.assertNotIn(TT_CLIENT_SECRET, text, name)
            cfg = w.store().read().config
            self.assertEqual((cfg['tiktok_open_id'], cfg['tiktok_display_name']), ('OPENID_OWNER', 'formform.studio'))
            self.assertEqual(cfg['tiktok_access_expires_at'], '2026-10-03T05:00:07Z')
            self.assertEqual(cfg['tiktok_refresh_expires_at'], '2027-10-02T05:00:07Z')

    def test_sends_the_documented_token_request_once(self):
        with World() as w:
            connect(w)
            [call] = w.srv.named('POST', TOKEN)
            self.assertEqual(call.form(), {'client_key': TT_CLIENT_KEY, 'client_secret': TT_CLIENT_SECRET,
                                           'code': TT_CODE, 'grant_type': 'authorization_code',
                                           'redirect_uri': config.TIKTOK_REDIRECT_URI})
            self.assertEqual(header(call, 'Content-Type'), 'application/x-www-form-urlencoded')

    def test_a_failed_exchange_is_not_retried_and_stores_nothing(self):
        with World() as w:
            w.tt_token_status = 400
            with self.assertRaises(TiktokAuthFailed) as cm:
                connect(w)
            self.assertEqual(len(w.srv.named('POST', TOKEN)), 1)               # kod tek kullanımlık
            for secret in (TT_CLIENT_SECRET, TT_CODE):                          # sunucu bunları geri yansıtır
                self.assertNotIn(secret, str(cm.exception))
            self.assertEqual(veri_files(w.remote), {})

    def test_a_code_cannot_be_used_twice(self):
        with World() as w:
            connect(w)
            with self.assertRaises(TiktokAuthFailed):
                tiktok.connect(w.store(), TT_CODE, NOW, log=lambda *_: None)

    def test_a_different_account_does_not_silently_replace_the_connected_one(self):
        with World() as w:
            connect(w)
            sealed = w.store().read().sealed_tiktok
            w.tt_user = {'open_id': 'OPENID_STRANGER', 'display_name': 'someone.else'}
            with self.assertRaises(TiktokAuthFailed) as cm:
                connect(w)
            self.assertIn('farklı', str(cm.exception))
            self.assertEqual(w.store().read().sealed_tiktok, sealed)           # bağlı hesap yerinde kaldı
            self.assertEqual(w.store().read().config['tiktok_open_id'], 'OPENID_OWNER')
            connect(w, replace=True)                                           # bilerek değiştirme mümkün
            self.assertEqual(w.store().read().config['tiktok_open_id'], 'OPENID_STRANGER')

    def test_reconnecting_the_same_account_is_fine(self):
        with World() as w:
            connect(w)
            connect(w)
            self.assertEqual(w.store().read().config['tiktok_display_name'], 'formform.studio')

    def test_missing_credentials_stop_before_any_request(self):
        with World(env={'TIKTOK_CLIENT_SECRET': ''}) as w:
            with self.assertRaises(CredentialsMissing) as cm:
                connect(w)
            self.assertIn('TIKTOK_CLIENT_SECRET', str(cm.exception))
            self.assertEqual(w.log(), [])

    def test_a_response_without_tokens_is_rejected(self):
        with World() as w:
            w.srv.route('POST', TOKEN, lambda c, m: (200, {'error': 'invalid_grant', 'error_description': 'yok'}))
            with self.assertRaises(TiktokAuthFailed) as cm:
                connect(w)
            self.assertIn('jeton yok', str(cm.exception))


class Tokens(unittest.TestCase):
    def test_a_fresh_token_is_used_without_calling_tiktok(self):
        with World() as w:
            connect(w)
            before = len(w.srv.calls)
            token = tiktok.access_token(w.store(), NOW + timedelta(hours=1))
            self.assertIn(token, w._tt_valid)
            self.assertEqual(len(w.srv.calls), before)

    def test_an_expiring_token_is_refreshed_and_the_rotated_refresh_token_is_saved(self):
        with World() as w:
            w.tt_rotate = True
            connect(w)
            first_refresh = w._tt_refresh
            later = NOW + timedelta(hours=23, minutes=55)                       # bitmesine 5 dk kala
            token = tiktok.access_token(w.store(), later)
            self.assertEqual(len(w.srv.named('POST', TOKEN)), 2)
            self.assertNotEqual(w._tt_refresh, first_refresh)                    # TikTok yenileme jetonunu değiştirdi
            saved = tiktok.load(w.store().read())
            self.assertEqual((saved['access_token'], saved['refresh_token']), (token, w._tt_refresh))
            # yeni jetonla bir sonraki çağrı yenilemeye gerek duymaz, değişen yenileme jetonu zincirde
            tiktok.access_token(w.store(), later + timedelta(hours=1))
            self.assertEqual(len(w.srv.named('POST', TOKEN)), 2)
            for name, text in veri_files(w.remote).items():
                self.assertNotIn(w._tt_refresh, text, name)

    def test_a_failed_refresh_is_not_retried_and_keeps_the_stored_tokens(self):
        with World() as w:
            connect(w)
            sealed = w.store().read().sealed_tiktok
            w._tt_refresh = 'rft.someone.else'                                   # TikTok eski jetonu artık tanımıyor
            with self.assertRaises(TiktokAuthFailed):
                tiktok.access_token(w.store(), NOW + timedelta(hours=24))
            self.assertEqual(len(w.srv.named('POST', TOKEN)), 2)                 # bağlanma + tek yenileme
            self.assertEqual(w.store().read().sealed_tiktok, sealed)

    def test_an_expired_refresh_token_asks_to_reconnect_without_calling_tiktok(self):
        with World() as w:
            connect(w)
            calls = len(w.srv.calls)
            with self.assertRaises(TiktokAuthFailed) as cm:
                tiktok.access_token(w.store(), NOW + timedelta(days=366))
            self.assertIn('yeniden bağlanın', str(cm.exception))
            self.assertEqual(len(w.srv.calls), calls)

    def test_losing_the_new_refresh_token_is_reported_with_how_to_recover(self):
        with World() as w:
            w.tt_rotate = True
            connect(w)
            with mock.patch('ops.tiktok.save', side_effect=StoreError('git push reddedildi')):
                with self.assertRaises(TiktokAuthFailed) as cm:
                    tiktok.access_token(w.store(), NOW + timedelta(hours=24))
            self.assertIn('yeniden bağlanın', cm.exception.hint)

    def test_not_connected_or_unreadable_tokens_say_so(self):
        with World() as w:
            with self.assertRaises(TiktokNotConnected):
                tiktok.access_token(w.store(), NOW)
            connect(w)
            with mock.patch.dict(os.environ, {'TIKTOK_CLIENT_SECRET': 'baska-bir-gizli-deger-0123456789'}):
                with self.assertRaises(TiktokNotConnected) as cm:                 # gizli değer değişmiş: açılamaz
                    tiktok.access_token(w.store(), NOW)
                self.assertIn('açılamadı', str(cm.exception))


class DraftUpload(unittest.TestCase):
    def test_uploads_a_draft_and_records_it_without_marking_it_published(self):
        with World([release(1)]) as w:
            connect(w)
            out = run(w)
            self.assertEqual(out, {'video_id': 'FF_20261002_060100', 'tiktok_publish_id': 'v_inbox_file~v2.123',
                                   'status': 'drafted'})
            log = w.log()
            order = [log.index(x) for x in ('GET /gh/repos/o/r/releases', 'GET /blob/video.mp4', f'POST {INIT}',
                                            f'PUT {PUT}', f'POST {STATUS}')]
            self.assertEqual(order, sorted(order), f'sıra bozuk: {log}')
            self.assertEqual([c for c in log if '/cld/' in c], [], 'doğrudan yükleme: Cloudinary gerekmez')

            [r] = rows(w)
            self.assertEqual((r['video_id'], r['status'], r['tiktok_id']),
                             ('FF_20261002_060100', 'drafted', 'v_inbox_file~v2.123'))
            self.assertEqual(r['published_at'], '')                              # paylaşımı kullanıcı yapar: zaman belli değil
            self.assertEqual((r['structure'], r['palette']), ('spiral form', 'violet-pink'))
            self.assertEqual(w.store().read().videos, [])                        # Instagram kaydına dokunulmadı
            self.assertTrue(w.releases[501]['name'].startswith('render '))       # release adı Instagram'ındır

    def test_sends_the_documented_requests(self):
        with World([release(1)]) as w:
            connect(w)
            run(w)
            token = next(iter(w._tt_valid))
            init = w.srv.named('POST', INIT)[0]
            self.assertEqual(header(init, 'Authorization'), f'Bearer {token}')
            self.assertEqual(init.json(), {'source_info': {'source': 'FILE_UPLOAD', 'video_size': len(VIDEO_BYTES),
                                                           'chunk_size': len(VIDEO_BYTES), 'total_chunk_count': 1}})
            range_header, body = w.tt_uploaded[0]
            self.assertEqual(range_header, f'bytes 0-{len(VIDEO_BYTES) - 1}/{len(VIDEO_BYTES)}')
            self.assertEqual(body, VIDEO_BYTES)
            put = w.srv.named('PUT', PUT)[0]
            self.assertEqual(header(put, 'Content-Type'), 'video/mp4')
            self.assertEqual(w.srv.named('POST', STATUS)[0].json(), {'publish_id': 'v_inbox_file~v2.123'})

    def test_the_claim_is_on_record_before_anything_is_uploaded(self):
        with World([release(1)]) as w:
            connect(w)
            seen = {}

            def spy(c, m):
                seen['rows'] = [(r['video_id'], r['status'], r['tiktok_id']) for r in w.store().read().ledger('tiktok').videos]
                return 200, {'data': {'publish_id': 'P1', 'upload_url': f'{w.srv.base}/tt-upload/abc?upload_token=X'},
                             'error': {'code': 'ok'}}
            w.srv.route('POST', INIT, spy)
            run(w)
            self.assertEqual(seen['rows'], [('FF_20261002_060100', 'claimed', '')])

    def test_waits_until_the_draft_reaches_the_inbox(self):
        with World([release(1)]) as w:
            connect(w)
            w.tt_statuses = ['PROCESSING_UPLOAD', 'PROCESSING_UPLOAD', 'SEND_TO_USER_INBOX']
            sleeps = []
            run(w, sleeps=sleeps)
            self.assertEqual(sleeps, [10, 10, 10])
            self.assertEqual(len(w.srv.named('POST', STATUS)), 3)

    def test_the_second_run_never_uploads_the_same_video_again(self):
        with World([release(1)]) as w:
            connect(w)
            run(w)
            with self.assertRaises(NotReady):
                run(w)
            self.assertEqual(len(w.srv.named('POST', INIT)), 1)
            self.assertEqual(len(rows(w)), 1)
            self.assertFalse(ledger.unclaim(w.store(), 'tiktok', 'FF_20261002_060100'))   # taslak var: silinemez

    def test_a_failed_processing_keeps_the_claim_and_is_not_retried(self):
        with World([release(1)]) as w:
            connect(w)
            w.tt_statuses = ['PROCESSING_UPLOAD', 'FAILED']
            with self.assertRaises(TiktokFailed) as cm:
                run(w)
            self.assertIn('file_format_check_failed', str(cm.exception))
            self.assertEqual([r['status'] for r in rows(w)], ['claimed'])
            with self.assertRaises(NotReady):
                run(w)
            self.assertEqual(len(w.srv.named('POST', INIT)), 1)

    def test_giving_up_after_the_max_wait_is_uncertain(self):
        with World([release(1)]) as w:
            connect(w)
            w.tt_statuses = ['PROCESSING_UPLOAD']
            with self.assertRaises(TiktokUncertain) as cm:
                run(w)
            self.assertIn('10 dakikada', str(cm.exception))
            self.assertEqual([r['status'] for r in rows(w)], ['claimed'])

    def test_a_failed_init_is_retried_and_the_claim_is_released_when_it_keeps_failing(self):
        with World([release(1)]) as w:
            connect(w)
            w.tt_init_status = 500
            sleeps = []
            with self.assertRaises(TiktokFailed) as cm:
                run(w, sleeps=sleeps)
            self.assertEqual(len(w.srv.named('POST', INIT)), 3)                  # yeni oturumla 3 deneme
            self.assertEqual(w.srv.named('PUT', PUT), [])                        # dosya hiç gitmedi
            self.assertEqual(sleeps, [5, 20])
            self.assertEqual(rows(w), [])                                        # üstlenme serbest bırakıldı
            self.assertIn('serbest bırakıldı', cm.exception.hint)

    def test_a_transient_init_failure_recovers_within_the_same_run(self):
        with World([release(1)]) as w:
            connect(w)
            w.tt_init_plan = [500, 200]
            out = run(w)
            self.assertEqual(out['status'], 'drafted')
            self.assertEqual(len(w.srv.named('POST', INIT)), 2)
            self.assertEqual(len(w.srv.named('PUT', PUT)), 1)

    def test_a_failed_file_send_is_uncertain_never_retried_and_hides_the_upload_key(self):
        with World([release(1)]) as w:
            connect(w)
            w.tt_upload_status = 500
            with self.assertRaises(TiktokUncertain) as cm:
                run(w)
            self.assertNotIn('SECRETUPLOADTOKEN', str(cm.exception))
            self.assertEqual(len(w.srv.named('PUT', PUT)), 1)
            self.assertEqual([r['status'] for r in rows(w)], ['claimed'])

    def test_a_404_from_the_upload_host_is_retried_with_a_fresh_session(self):
        """Canlıdan ders: TikTok'un yükleme sunucusu bir kez 404 döndü, ikinci denemede aynı video geçti. 404 = oturum
        yok, dosya ulaşmadı: yeni oturumla tekrar denemek çift taslak yaratamaz."""
        with World([release(1)]) as w:
            connect(w)
            w.tt_upload_plan = [404, 201]
            sleeps = []
            out = run(w, sleeps=sleeps)
            self.assertEqual(out['status'], 'drafted')
            self.assertEqual(len(w.srv.named('POST', INIT)), 2)                  # her deneme yeni oturum
            self.assertEqual(len(w.srv.named('PUT', PUT)), 2)
            self.assertEqual(len(w.tt_uploaded), 1)                              # dosya TikTok'a yalnız bir kez ulaştı
            self.assertEqual([r['status'] for r in rows(w)], ['drafted'])
            self.assertEqual(len(w.issues), 1)                                   # tek başlık bildirimi
            self.assertEqual(sleeps[0], 5)

    def test_when_every_attempt_proves_nothing_arrived_the_next_run_heals_by_itself(self):
        with World([release(1)]) as w:
            connect(w)
            w.tt_upload_plan = [404, 404, 404]
            sleeps = []
            with self.assertRaises(TiktokFailed) as cm:
                run(w, sleeps=sleeps)
            self.assertEqual(len(w.srv.named('PUT', PUT)), 3)
            self.assertEqual(sleeps, [5, 20])
            self.assertEqual(rows(w), [])                                        # takılı satır yok
            self.assertIn('kendiliğinden', cm.exception.hint)
            self.assertEqual(w.tt_uploaded, [])
            # Sonraki tur (Worker saati ya da nöbetçi) elle müdahale olmadan tamamlar
            out = run(w)
            self.assertEqual(out['status'], 'drafted')
            self.assertEqual(len(w.tt_uploaded), 1)
            self.assertEqual([r['status'] for r in rows(w)], ['drafted'])

    def test_other_upload_errors_are_still_never_retried_because_a_draft_may_exist(self):
        for code in (500, 502, 403, 400):
            with World([release(1)]) as w:
                connect(w)
                w.tt_upload_plan = [code, 201]
                with self.assertRaises(TiktokUncertain):
                    run(w)
                self.assertEqual(len(w.srv.named('PUT', PUT)), 1, code)           # ikinci PUT yok
                self.assertEqual(len(w.srv.named('POST', INIT)), 1, code)
                self.assertEqual([r['status'] for r in rows(w)], ['claimed'], code)   # satır takılı: taslak olabilir

    def test_a_claim_that_cannot_be_released_is_reported_without_hiding_the_failure(self):
        with World([release(1)]) as w:
            connect(w)
            w.tt_upload_plan = [404, 404, 404]
            lines = []
            with mock.patch('ops.publish.ledger.unclaim', side_effect=StoreError('git push reddedildi')):
                with self.assertRaises(TiktokFailed) as cm:
                    publish.run(w.store(), platform='tiktok', clock=CLOCK, sleep=lambda s: None, log=lines.append,
                                workdir=tempfile.mkdtemp())
            self.assertNotIn('serbest bırakıldı', cm.exception.hint)               # yalansöylemez: bırakılamadı
            self.assertTrue(any('serbest bırakılamadı' in l for l in lines))
            self.assertEqual([r['status'] for r in rows(w)], ['claimed'])

    def test_not_connected_stops_before_touching_anything(self):
        with World([release(1)]) as w:
            with self.assertRaises(TiktokNotConnected) as cm:
                run(w)
            self.assertIn('Connect TikTok', cm.exception.hint)
            self.assertEqual(w.log(), [])

    def test_a_video_over_the_single_chunk_limit_fails_before_claiming(self):
        with World([release(1)]) as w:
            connect(w)
            with mock.patch.object(config, 'TIKTOK_MAX_SINGLE_CHUNK', 100):
                with self.assertRaises(OpsError) as cm:
                    run(w)
            self.assertIn('Hiçbir şey yüklenmedi', str(cm.exception))
            self.assertEqual(rows(w), [])                                         # satır takılı kalmadı
            self.assertEqual(w.srv.named('POST', INIT), [])

    def test_losing_the_claim_race_stops_before_any_upload(self):
        with World([release(1)]) as w:
            connect(w)
            with mock.patch('ops.publish.ledger.claim', return_value=False):
                with self.assertRaises(NotReady):
                    run(w)
            self.assertEqual(w.srv.named('POST', INIT), [])

    def test_store_failure_after_the_draft_reports_the_publish_id(self):
        with World([release(1)]) as w:
            connect(w)
            with mock.patch('ops.publish.ledger.settle', side_effect=StoreError('git push reddedildi')):
                with self.assertRaises(StoreError) as cm:
                    run(w)
            self.assertIn('v_inbox_file~v2.123', str(cm.exception))
            self.assertIn('tekrar yüklemeyin', cm.exception.hint)
            self.assertEqual([r['status'] for r in rows(w)], ['claimed'])

    def test_the_caption_is_shown_for_pasting_and_written_to_the_job_summary(self):
        with World([release(1)]) as w:
            connect(w)
            summary = os.path.join(tempfile.mkdtemp(), 'summary.md')
            lines = []
            with mock.patch.dict(os.environ, {'GITHUB_STEP_SUMMARY': summary}):
                publish.run(w.store(), platform='tiktok', clock=CLOCK, sleep=lambda s: None, log=lines.append,
                            workdir=tempfile.mkdtemp())
            self.assertTrue(any(l.startswith('A temporary geometry.') for l in lines))
            with open(summary, encoding='utf-8') as f:
                text = f.read()
            self.assertIn('A temporary geometry.', text)
            self.assertIn('Gelen Kutusu', text)

    def test_the_caption_reaches_the_owner_as_a_copyable_notification(self):
        with World([release(1)]) as w:
            connect(w)
            run(w)
            [issue] = w.issues
            self.assertEqual(issue['title'], '[FORMFORM] TikTok taslağı hazır: FF_20261002_060100')
            self.assertEqual(issue['assignees'], ['o'])                           # depo sahibine atanır: e-posta + uygulama bildirimi
            self.assertEqual(issue['state'], 'closed')                            # liste kalabalıklaşmaz
            body = issue['body']
            self.assertIn('Gelen Kutusu', body)
            self.assertIn('```text\nA temporary geometry.\n\nform/form — study.\n\n#formform #abstractart\n```', body)

    def test_a_notification_that_cannot_be_sent_does_not_fail_the_upload(self):
        with World([release(1)]) as w:
            connect(w)
            w.srv.route('POST', r'/gh/repos/o/r/issues', lambda c, m: (500, {'message': 'boom'}))
            lines = []
            out = publish.run(w.store(), platform='tiktok', clock=CLOCK, sleep=lambda s: None, log=lines.append,
                              workdir=tempfile.mkdtemp())
            self.assertEqual(out['status'], 'drafted')
            self.assertEqual([r['status'] for r in rows(w)], ['drafted'])         # taslak ve kayıt tamam
            self.assertTrue(any('başlık bildirimi gönderilemedi' in l for l in lines))
            self.assertTrue(any(l.startswith('A temporary geometry.') for l in lines))   # başlık günlükte yine var

    def test_nothing_is_sent_when_nothing_was_drafted(self):
        with World([release(1)]) as w:
            connect(w)
            run(w, dry_run=True)
            self.assertEqual(w.issues, [])
        with World([release(1)]) as w:
            connect(w)
            w.tt_statuses = ['FAILED']
            with self.assertRaises(TiktokFailed):
                run(w)
            self.assertEqual(w.issues, [])
        with World([release(1)]) as w:                                            # üstlenilmiş video tekrar yüklenmez, tekrar bildirilmez
            connect(w)
            run(w)
            with self.assertRaises(NotReady):
                run(w)
            self.assertEqual(len(w.issues), 1)

    def test_dry_run_checks_everything_and_changes_nothing(self):
        with World([release(1)]) as w:
            connect(w)
            out = run(w, dry_run=True)
            self.assertEqual(out, {'dry_run': True, 'video_id': 'FF_20261002_060100'})
            log = w.log()
            self.assertIn('GET /tt/v2/user/info/', log)
            self.assertIn('GET /blob/video.mp4', log)
            self.assertEqual([c for c in log if INIT in c or PUT in c], [])
            self.assertEqual(rows(w), [])

    def test_the_three_platforms_post_the_same_render_independently(self):
        with World([release(1)]) as w:
            connect(w)
            publish.run(w.store(), clock=CLOCK, sleep=lambda s: None, log=lambda *_: None, workdir=tempfile.mkdtemp())
            run(w)
            data = w.store().read()
            self.assertEqual([r['instagram_id'] for r in data.videos], ['MEDIA777'])
            self.assertEqual([r['status'] for r in data.ledger('tiktok').videos], ['drafted'])
            self.assertEqual(w.releases[501]['name'], 'published FF_20261002_060100')   # Instagram'ın durumu


class Stats(unittest.TestCase):
    def test_lists_the_owners_own_videos_with_their_counts(self):
        with World() as w:
            connect(w)
            w.tt_videos = [{'id': '711', 'title': 'Study 1', 'create_time': 1759400000, 'view_count': 120, 'like_count': 9,
                            'comment_count': 2, 'share_count': 1}]
            token = tiktok.access_token(w.store(), NOW)
            self.assertEqual(tiktok.list_videos(token)[0]['view_count'], 120)
            call = w.srv.named('POST', r'/tt/v2/video/list/')[0]
            self.assertIn('view_count', call.query['fields'])
            self.assertEqual(call.json(), {'max_count': 20})
            self.assertEqual(tiktok.list_videos(token, 500) and w.srv.named('POST', r'/tt/v2/video/list/')[1].json(),
                             {'max_count': 20})                                  # TikTok üst sınırı 20


class CommandLine(unittest.TestCase):
    def test_connect_reads_the_code_from_the_environment_never_the_command_line(self):
        with World() as w:
            out = io.StringIO()
            with mock.patch.dict(os.environ, {'TIKTOK_CODE': TT_CODE}), contextlib.redirect_stdout(out):
                self.assertEqual(cli.main(['tiktok-connect']), 0)
            self.assertIn('TikTok bağlandı: formform.studio', out.getvalue())
            self.assertNotIn(TT_CODE, out.getvalue())
            self.assertEqual(w.store().read().config['tiktok_display_name'], 'formform.studio')

    def test_connect_without_a_code_fails_clearly(self):
        with World() as w:
            out = io.StringIO()
            with mock.patch.dict(os.environ, {'TIKTOK_CODE': ''}), contextlib.redirect_stdout(out):
                self.assertEqual(cli.main(['tiktok-connect']), 1)
            self.assertIn('yetkilendirme kodu yok', out.getvalue())
            self.assertEqual(w.log(), [])

    def test_stats_prints_one_line_per_video(self):
        with World() as w:
            tiktok.connect(w.store(), TT_CODE, datetime.now(timezone.utc), log=lambda *_: None)
            w.tt_videos = [{'id': '711', 'title': 'Study 1', 'create_time': 1759400000, 'view_count': 120,
                            'like_count': 9, 'comment_count': 2, 'share_count': 1}]
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(cli.main(['tiktok-stats']), 0)
            self.assertIn('hesabın 1 videosu okundu', out.getvalue())
            self.assertIn('izlenme 120 | beğeni 9 | yorum 2 | paylaşım 1 | Study 1', out.getvalue())


if __name__ == '__main__':
    unittest.main()
