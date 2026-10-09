"""TikTok öğrenme: paylaşılan taslağı bulma, 24/72 saatlik ölçüm, skor ve ağırlık.

Taslak modunda paylaşımı kullanıcı yapar, sistem bunu sonradan TikTok'tan öğrenir. Asıl güvenceler: belirsiz
eşleşme yapılmaz (bir videonun başarısı başka tarife yazılmasın), öğrenme turu jetonu asla yenilemez, yapacak iş
yokken TikTok'a hiç gidilmez ve kayıt işlenmez, Instagram'ın verisine dokunulmaz."""
import contextlib
import io
import math
import os
import subprocess
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from ops import cli, config, platforms, publish, tiktok, tiktok_sync
from ops.errors import NotReady
from ops.tests.fakes import TT_CODE, World, release

NOW = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)
LIST = r'/tt/v2/video/list/'
STATUS = r'/tt/v2/post/publish/status/fetch/'
TOKEN = r'/tt/v2/oauth/token/'
CAPTION = 'This form will not last.\n\nProcedural light sculpture.\n\n#formform #minimalart'


def iso(hours_ago):
    return (NOW - timedelta(hours=hours_ago)).strftime('%Y-%m-%dT%H:%M:%S.000Z')


def epoch(hours_ago):
    return int((NOW - timedelta(hours=hours_ago)).timestamp())


def draft(i, rendered_hours_ago, **kw):
    """Gelen kutusuna yüklenmiş, henüz paylaşıldığı bilinmeyen satır (`publish.run_tiktok` böyle bırakır)."""
    row = {c: '' for c in platforms.get('tiktok').columns}
    row.update(video_id=f'FF_{i}', created_at=iso(rendered_hours_ago), structure='floating core',
               palette='graphite-silver', motion_quality='m', lighting='l', loop_strategy='seamless_8s',
               materials='a, b', transformations='t1', caption=CAPTION, selection_mode='exploitation',
               tiktok_id=f'v_inbox_file~v2.{i}', status='drafted')
    row.update(kw)
    return row


def post(video_id, hours_ago, title='', duration=8, views=0, likes=0, comments=0, shares=0):
    return {'id': video_id, 'title': title, 'video_description': title, 'create_time': epoch(hours_ago),
            'duration': duration, 'view_count': views, 'like_count': likes, 'comment_count': comments,
            'share_count': shares}


def seed(w, rows):
    w.store().transaction(lambda d: d.ledger('tiktok').videos.extend(rows), 'tohum')


def rows(w):
    return w.store().read().ledger('tiktok').videos


def commits(w):
    return int(subprocess.run(['git', '-C', w.remote, 'rev-list', '--count', 'veri'], capture_output=True,
                              text=True).stdout or 0)


def connect(w, now=NOW):
    tiktok.connect(w.store(), TT_CODE, now, log=lambda *_: None)
    return tiktok.access_token(w.store(), now)


def sync(w, token, now=NOW, **kw):
    lines = []
    out = tiktok_sync.run(w.store(), token, now, log=lines.append, **kw)
    return out, lines


def old_formula(views, likes=0, comments=0, shares=0):
    """İzlenme süresi olmayan videonun skoru: %65 erişim, %35 etkileşim (kaydetme yok)."""
    reach = min(100.0, 25 * math.log10(views + 1))
    engagement = min(100.0, (likes + comments * 3 + shares * 4) / views * 500) * min(1.0, views / 100)
    return math.floor((reach * 0.65 + engagement * 0.35) * 100 + 0.5) / 100


class Matching(unittest.TestCase):
    """`match_posts`: hangi kanıtla, hangi taslak hangi videoya bağlanır."""

    def test_tiktoks_own_answer_wins_over_everything_else(self):
        a, b = draft(1, 40), draft(2, 30)
        videos = [post('901', 20, title='başka bir metin'), post('902', 10, title=CAPTION)]
        statuses = {'FF_1': {'status': 'PUBLISH_COMPLETE', 'publicaly_available_post_id': [902]},
                    'FF_2': {'status': 'SEND_TO_USER_INBOX'}}
        out = tiktok_sync.match_posts([a, b], videos, statuses, set())
        self.assertIn(('FF_1', '902', tiktok_sync.EXACT), [(r['video_id'], v['id'], p) for r, v, p in out])

    def test_a_pasted_caption_identifies_the_post(self):
        a = draft(1, 40, caption='A thing that cannot be held.\n\nx\n\n#formform')
        b = draft(2, 30)
        videos = [post('902', 5, title='This form will not last. Procedural light sculpture. #formform #minimalart')]
        [(row, video, proof)] = tiktok_sync.match_posts([a, b], videos, {}, set())
        self.assertEqual((row['video_id'], video['id'], proof), ('FF_2', '902', tiktok_sync.BY_CAPTION))

    def test_without_a_caption_the_only_post_of_the_same_length_after_the_render_is_taken(self):
        a = draft(1, 40, loop_strategy='seamless_15s')
        b = draft(2, 6)
        videos = [post('901', 20, title='#formform', duration=15)]            # FF_2 render edilmeden önce paylaşılmış
        [(row, video, proof)] = tiktok_sync.match_posts([a, b], videos, {}, set())
        self.assertEqual((row['video_id'], video['id'], proof), ('FF_1', '901', tiktok_sync.BY_TIME))

    def test_caption_matches_free_the_remaining_post_for_the_remaining_draft(self):
        """Canlıdaki durum: ilk video başlıksız, ikincisi başlıklı paylaşıldı."""
        a = draft(1, 40, loop_strategy='seamless_15s', caption='A thing that cannot be held.\n\nx')
        b = draft(2, 30)
        videos = [post('902', 2, title='This form will not last. Procedural', duration=8),
                  post('901', 26, title='#formform', duration=15)]
        out = tiktok_sync.match_posts([a, b], videos, {}, set())
        self.assertEqual(sorted((r['video_id'], v['id']) for r, v, _ in out), [('FF_1', '901'), ('FF_2', '902')])

    def test_two_drafts_that_could_be_the_same_post_are_both_left_alone(self):
        a, b = draft(1, 40), draft(2, 30)                                     # aynı başlık, aynı süre
        self.assertEqual(tiktok_sync.match_posts([a, b], [post('901', 5, title=CAPTION)], {}, set()), [])
        self.assertEqual(tiktok_sync.match_posts([a, b], [post('901', 5)], {}, set()), [])

    def test_two_posts_that_could_be_the_same_draft_are_left_alone(self):
        videos = [post('901', 5), post('902', 4)]
        self.assertEqual(tiktok_sync.match_posts([draft(1, 40)], videos, {}, set()), [])

    def test_a_post_older_than_the_render_or_of_another_length_is_never_matched(self):
        self.assertEqual(tiktok_sync.match_posts([draft(1, 10)], [post('901', 20, title=CAPTION)], {}, set()), [])
        self.assertEqual(tiktok_sync.match_posts([draft(1, 40)], [post('901', 5, title=CAPTION, duration=30)], {}, set()), [])

    def test_a_post_of_unknown_length_is_taken_only_when_nothing_else_could_be_it(self):
        video = post('901', 5)
        del video['duration']                                                 # TikTok süreyi vermediyse zaman yeter
        [(row, got, proof)] = tiktok_sync.match_posts([draft(1, 40)], [video], {}, set())
        self.assertEqual((row['video_id'], got['id'], proof), ('FF_1', '901', tiktok_sync.BY_TIME))
        self.assertEqual(tiktok_sync.match_posts([draft(1, 40), draft(2, 30)], [video], {}, set()), [])

    def test_a_post_already_tied_to_another_recipe_is_not_reused(self):
        videos = [post('901', 5, title=CAPTION)]
        self.assertEqual(tiktok_sync.match_posts([draft(1, 40)], videos, {}, {'901'}), [])
        statuses = {'FF_1': {'status': 'PUBLISH_COMPLETE', 'publicaly_available_post_id': ['901']}}
        self.assertEqual(tiktok_sync.match_posts([draft(1, 40)], videos, statuses, {'901'}), [])


class Sync(unittest.TestCase):
    def test_a_shared_draft_becomes_published_with_its_real_time_and_tiktok_id(self):
        with World() as w:
            token = connect(w)
            seed(w, [draft(1, 40)])
            w.tt_posts['v_inbox_file~v2.1'] = {'status': 'PUBLISH_COMPLETE', 'publicaly_available_post_id': [7693534094738672928]}
            w.tt_videos = [post(7693534094738672928, 3, title=CAPTION, views=304, likes=3)]
            out, lines = sync(w, token)
            self.assertEqual(out['matched'], 1)
            [r] = rows(w)
            self.assertEqual((r['status'], r['tiktok_id'], r['publish_id']),
                             ('published', '7693534094738672928', 'v_inbox_file~v2.1'))
            self.assertEqual(r['published_at'], iso(3))                       # yüklendiği an değil, paylaşıldığı an
            self.assertEqual((r['views_24h'], r['views_72h'], r['performance_score']), ('', '', ''))   # daha 24 saat olmadı
            self.assertTrue(any('Paylaşılmış: FF_1 → TikTok 7693534094738672928' in ln and 'TikTok bildirdi' in ln
                                for ln in lines), lines)
            self.assertEqual(w.srv.named('POST', STATUS)[-1].json(), {'publish_id': 'v_inbox_file~v2.1'})

    def test_a_draft_still_in_the_inbox_stays_a_draft(self):
        with World() as w:
            token = connect(w)
            seed(w, [draft(1, 40)])
            before = commits(w)
            out, lines = sync(w, token)                                      # hesapta video yok
            self.assertEqual(out['matched'], 0)
            self.assertEqual((rows(w)[0]['status'], rows(w)[0]['published_at']), ('drafted', ''))
            self.assertEqual(commits(w), before, 'değişiklik yokken kayıt işlendi')
            self.assertTrue(any('Henüz paylaşılmamış ya da eşleşmedi: FF_1 (TikTok durumu: SEND_TO_USER_INBOX)' in ln
                                for ln in lines), lines)

    def test_a_status_that_cannot_be_read_falls_back_to_the_caption(self):
        with World() as w:
            token = connect(w)
            seed(w, [draft(1, 40)])
            w.tt_posts['v_inbox_file~v2.1'] = 400                              # eski yükleme kimliği artık sorulamıyor
            w.tt_videos = [post('901', 3, title=CAPTION)]
            out, lines = sync(w, token)
            self.assertEqual((out['matched'], rows(w)[0]['tiktok_id']), (1, '901'))
            self.assertTrue(any('yükleme durumu alınamadı' in ln for ln in lines), lines)
            self.assertTrue(any('başlıktan' in ln for ln in lines), lines)

    def test_counts_are_read_at_24_and_72_hours_and_the_score_uses_the_formula_without_watch_time(self):
        with World() as w:
            token = connect(w)
            seed(w, [draft(1, 60)])
            w.tt_videos = [post('901', 26, title=CAPTION, views=643, likes=2)]
            out, _ = sync(w, token)                                          # bulundu ve 24 saati geçmiş: aynı turda ölçülür
            self.assertEqual((out['matched'], out['measured'], out['scored']), (1, 1, 0))
            r = rows(w)[0]
            self.assertEqual((r['views_24h'], r['likes'], r['comments'], r['shares'], r['saves']), ('643', '2', '0', '0', ''))
            self.assertEqual((r['views_72h'], r['performance_score']), ('', ''))

            later = NOW + timedelta(hours=50)                                # paylaşımın 76. saati
            w.tt_videos = [dict(w.tt_videos[0], view_count=1200, like_count=30, comment_count=4, share_count=5)]
            token = tiktok.access_token(w.store(), later)
            out, lines = sync(w, token, now=later)
            self.assertEqual((out['matched'], out['measured'], out['scored']), (0, 1, 1))
            r = rows(w)[0]
            self.assertEqual((r['views_24h'], r['views_72h'], r['likes'], r['shares']), ('643', '1200', '30', '5'))
            self.assertEqual(float(r['performance_score']), old_formula(1200, likes=30, comments=4, shares=5))
            self.assertEqual(w.srv.named('POST', STATUS)[1:], [], 'yayınlanmış satır için yükleme durumu sorulmaz')
            self.assertTrue(any('Ölçüm: FF_1 72h (izlenme 1200)' in ln for ln in lines), lines)

            dna = w.store().read().ledger('tiktok').dna
            keys = {x['key'] for x in dna}
            self.assertLessEqual({'structure::floating core', 'palette::graphite-silver', 'format::seamless_8s',
                                  'hook::This form will not last.', 'tag::#minimalart'}, keys)
            self.assertEqual({x['sample_count'] for x in dna}, {'1'})

    def test_tiktok_learning_never_touches_instagrams_records(self):
        with World() as w:
            token = connect(w)
            seed(w, [draft(1, 100)])
            w.tt_videos = [post('901', 80, title=CAPTION, views=500, likes=5)]
            sync(w, token)
            data = w.store().read()
            self.assertEqual((data.videos, data.dna), ([], []))
            self.assertTrue(data.ledger('tiktok').dna)
            files = subprocess.run(['git', '-C', w.remote, 'ls-tree', '-r', '--name-only', 'veri'], capture_output=True,
                                   text=True).stdout.split()
            self.assertIn('tiktok/dna_weights.csv', files)

    def test_a_late_video_gets_72h_first_then_24h_and_a_third_run_changes_nothing(self):
        with World() as w:
            token = connect(w)
            seed(w, [draft(1, 100)])
            w.tt_videos = [post('901', 80, title=CAPTION, views=500, likes=5)]
            out, _ = sync(w, token)
            self.assertEqual((rows(w)[0]['views_72h'], rows(w)[0]['views_24h'], out['scored']), ('500', '', 1))
            sync(w, token)
            self.assertEqual(rows(w)[0]['views_24h'], '500')
            before, calls = commits(w), len(w.srv.calls)
            out, lines = sync(w, token)
            self.assertEqual(commits(w), before, 'değişiklik yokken kayıt işlendi')
            self.assertEqual(len(w.srv.calls), calls, 'yapacak iş yokken TikTok çağrıldı')
            self.assertEqual(lines, ['TikTok: aranacak taslak ya da zamanı gelen ölçüm yok.'])

    def test_a_video_missing_from_the_list_is_left_blank_and_tried_again(self):
        with World() as w:
            token = connect(w)
            seed(w, [draft(1, 60, tiktok_id='901', publish_id='v_inbox_file~v2.1', status='published',
                           published_at=iso(30))])
            out, lines = sync(w, token)                                      # silinmiş ya da gizlenmiş: listede yok
            self.assertEqual(out['measured'], 0)
            self.assertEqual(rows(w)[0]['views_24h'], '', 'sıfır yazmak videoyu yanlışlıkla ölçülmüş yapardı')
            self.assertTrue(any('Ölçüm alınamadı: FF_1 (24h)' in ln for ln in lines), lines)
            w.tt_videos = [post('901', 30, views=40)]
            sync(w, token)
            self.assertEqual(rows(w)[0]['views_24h'], '40')

    def test_dry_run_reports_what_it_found_and_writes_nothing(self):
        with World() as w:
            token = connect(w)
            seed(w, [draft(1, 60)])
            w.tt_videos = [post('901', 26, title=CAPTION, views=643)]
            before = commits(w)
            out, lines = sync(w, token, dry_run=True)
            self.assertEqual((out['matched'], out['measured'], out['dry_run']), (1, 1, True))
            self.assertEqual(commits(w), before)
            self.assertEqual(rows(w)[0]['status'], 'drafted')
            self.assertTrue(any('DENEME: kayıt YAZILMADI' in ln for ln in lines), lines)

    def test_claimed_rows_and_forgotten_old_drafts_are_not_looked_up(self):
        with World() as w:
            token = connect(w)
            days = config.TIKTOK_DRAFT_WATCH_DAYS
            seed(w, [draft(1, 24 * days + 5), draft(2, 3, tiktok_id='', status='claimed')])
            calls = len(w.srv.calls)
            _, lines = sync(w, token)
            self.assertEqual(len(w.srv.calls), calls)
            self.assertEqual(lines, ['TikTok: aranacak taslak ya da zamanı gelen ölçüm yok.'])

    def test_only_reading_calls_are_made(self):
        with World() as w:
            token = connect(w)
            seed(w, [draft(1, 60)])
            w.tt_videos = [post('901', 26, title=CAPTION, views=643)]
            start = len(w.srv.calls)
            sync(w, token)
            tiktok_calls = {c.path for c in w.srv.calls[start:] if c.path.startswith('/tt')}
            self.assertEqual(tiktok_calls, {'/tt/v2/video/list/', '/tt/v2/post/publish/status/fetch/'})

    def test_the_whole_way_from_upload_to_score(self):
        """Yükleme turunun bıraktığı gerçek satır, paylaşımdan sonra bulunur ve skorlanır."""
        with World([release(1, created='2026-10-10T03:30:00Z')]) as w:
            token = connect(w)
            publish.run(w.store(), platform='tiktok', clock=lambda: NOW, sleep=lambda *_: None, log=lambda *_: None)
            self.assertEqual(rows(w)[0]['status'], 'drafted')
            w.tt_posts['v_inbox_file~v2.123'] = {'status': 'PUBLISH_COMPLETE', 'publicaly_available_post_id': ['901']}
            w.tt_videos = [post('901', -1, title='A temporary geometry.', views=900, likes=20, shares=3)]   # 1 saat sonra paylaşıldı
            later = NOW + timedelta(hours=75)
            sync(w, tiktok.access_token(w.store(), later), now=later)
            r = rows(w)[0]
            self.assertEqual((r['status'], r['tiktok_id'], r['views_72h']), ('published', '901', '900'))
            self.assertEqual(float(r['performance_score']), old_formula(900, likes=20, shares=3))
            # bir sonraki yükleme turu bu videoyu yeniden yüklemez
            with self.assertRaises(NotReady):
                publish.run(w.store(), platform='tiktok', clock=lambda: later, sleep=lambda *_: None, log=lambda *_: None)
            self.assertEqual(len(w.tt_uploaded), 1)


class Tokens(unittest.TestCase):
    def test_the_stored_token_is_read_without_ever_refreshing(self):
        with World() as w:
            connect(w)
            calls = len(w.srv.named('POST', TOKEN))
            almost = NOW + timedelta(hours=23, minutes=58)
            self.assertIsNotNone(tiktok.stored_access_token(w.store().read(), almost))
            for when in (NOW + timedelta(hours=23, minutes=59, seconds=30), NOW + timedelta(hours=30)):
                self.assertIsNone(tiktok.stored_access_token(w.store().read(), when))
            self.assertEqual(len(w.srv.named('POST', TOKEN)), calls, 'öğrenme turu jetonu yeniledi')

    def test_every_upload_run_refreshes_so_the_token_never_dies_between_runs(self):
        with World() as w:
            connect(w)
            calls = len(w.srv.named('POST', TOKEN))

            def upload(now):
                with self.assertRaises(NotReady):                             # render yok: jeton adımından sonra durur
                    publish.run(w.store(), platform='tiktok', clock=lambda: now, sleep=lambda *_: None, log=lambda *_: None)

            upload(NOW + timedelta(hours=1))
            self.assertEqual(len(w.srv.named('POST', TOKEN)), calls, 'taze jeton boşuna yenilendi')
            upload(NOW + timedelta(hours=12))                                # 12 saat sonraki tur
            self.assertEqual(len(w.srv.named('POST', TOKEN)), calls + 1)
            self.assertEqual(w.store().read().config['tiktok_access_expires_at'], '2026-10-12T00:00:00Z')
            # bir sonraki tur kaçsa bile (24. saat) öğrenme turu jetonu 35. saatte hâlâ geçerli bulur
            self.assertIsNotNone(tiktok.stored_access_token(w.store().read(), NOW + timedelta(hours=35)))

    def test_the_video_list_follows_the_cursor_up_to_the_limit(self):
        with World() as w:
            token = connect(w)
            w.tt_videos = [post(str(i), i) for i in range(1, 46)]
            got = tiktok.list_videos(token, 60)
            self.assertEqual([v['id'] for v in got], [str(i) for i in range(1, 46)])
            bodies = [c.json() for c in w.srv.named('POST', LIST)]
            self.assertEqual(bodies, [{'max_count': 20}, {'max_count': 20, 'cursor': 20}, {'max_count': 20, 'cursor': 40}])
            self.assertEqual(len(tiktok.list_videos(token, 25)), 25)


class CommandLine(unittest.TestCase):
    def run_cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(['tiktok-sync', *args])
        return code, out.getvalue()

    def test_the_learning_run_finds_and_records_a_shared_draft(self):
        with World() as w:
            tiktok.connect(w.store(), TT_CODE, datetime.now(timezone.utc), log=lambda *_: None)
            row = draft(1, 0, created_at='2026-01-01T00:00:00.000Z')
            row['created_at'] = (datetime.now(timezone.utc) - timedelta(hours=5)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
            seed(w, [row])
            w.tt_videos = [dict(post('901', 0, title=CAPTION), create_time=int(datetime.now(timezone.utc).timestamp()) - 3600)]
            code, text = self.run_cli()
            self.assertEqual(code, 0, text)
            self.assertIn('Paylaşılmış: FF_1 → TikTok 901', text)
            self.assertEqual(rows(w)[0]['status'], 'published')

    def test_without_tiktok_the_learning_run_skips_quietly(self):
        with World() as w:                                                   # hiç bağlanılmamış
            code, text = self.run_cli()
            self.assertEqual(code, 0)
            self.assertIn('TikTok atlandı', text)
            self.assertEqual(w.log(), [])
        with World() as w, mock.patch.dict(os.environ, {'TIKTOK_CLIENT_KEY': '', 'TIKTOK_CLIENT_SECRET': ''}):
            code, text = self.run_cli()                                      # gizli değerler tanımlı değil
            self.assertEqual(code, 0)
            self.assertIn('TikTok atlandı', text)
            self.assertNotIn('::error::', text)

    def test_an_expired_token_is_skipped_not_refreshed_by_the_learning_run(self):
        with World() as w:
            tiktok.connect(w.store(), TT_CODE, datetime.now(timezone.utc) - timedelta(hours=30), log=lambda *_: None)
            seed(w, [draft(1, 40)])
            calls = len(w.srv.calls)
            code, text = self.run_cli()
            self.assertEqual(code, 0)
            self.assertIn('erişim jetonunun süresi dolmuş', text)
            self.assertEqual(len(w.srv.calls), calls, 'süresi dolmuş jetonla TikTok çağrıldı ya da jeton yenilendi')

    def test_the_manual_run_refreshes_an_expired_token(self):
        with World() as w:
            tiktok.connect(w.store(), TT_CODE, datetime.now(timezone.utc) - timedelta(hours=30), log=lambda *_: None)
            code, text = self.run_cli('--refresh')
            self.assertEqual(code, 0, text)
            self.assertEqual(len(w.srv.named('POST', TOKEN)), 2)

    def test_a_rejected_token_does_not_fail_the_learning_run_but_fails_the_manual_one(self):
        with World() as w:
            tiktok.connect(w.store(), TT_CODE, datetime.now(timezone.utc), log=lambda *_: None)
            seed(w, [draft(1, 0, created_at=(datetime.now(timezone.utc) - timedelta(hours=5)).strftime('%Y-%m-%dT%H:%M:%S.000Z'))])
            w._tt_valid.clear()                                              # jeton bu sırada başka turda yenilendi
            code, text = self.run_cli()
            self.assertEqual(code, 0)
            self.assertIn('erişim jetonu kabul edilmedi', text)
            code, text = self.run_cli('--refresh')
            self.assertEqual(code, 1)
            self.assertIn('::error::TikTok okunamadı', text)

    def test_dry_run_from_the_command_line_writes_nothing(self):
        with World() as w:
            tiktok.connect(w.store(), TT_CODE, datetime.now(timezone.utc), log=lambda *_: None)
            seed(w, [draft(1, 0, created_at=(datetime.now(timezone.utc) - timedelta(hours=5)).strftime('%Y-%m-%dT%H:%M:%S.000Z'))])
            w.tt_videos = [dict(post('901', 0, title=CAPTION), create_time=int(datetime.now(timezone.utc).timestamp()) - 3600)]
            code, text = self.run_cli('--refresh', '--dry-run')
            self.assertEqual(code, 0, text)
            self.assertIn('DENEME: kayıt YAZILMADI', text)
            self.assertEqual(rows(w)[0]['status'], 'drafted')


if __name__ == '__main__':
    unittest.main()
