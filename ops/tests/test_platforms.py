"""Platform katmanı: her platformun verisi ayrı, yayın durumu ayrı, Instagram eskisi gibi.

Asıl güvence üç şey: (1) bir platformun kaydı ötekinin dosyasına dokunmaz, (2) üstlenme atomiktir (aynı anda iki
akış aynı videoyu paylaşamaz), (3) Instagram'ın yayın yolu bu katmandan etkilenmez.
"""
import subprocess
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from ops import cli, ledger, platforms, publish, scoring
from ops.errors import OpsError, StoreError
from ops.store import DNA_COLUMNS, VIDEO_COLUMNS, Store
from ops.tests.fakes import World, release
from ops.tests.test_core import bare_remote, count

NOW = datetime(2026, 10, 5, 6, 0, 0, tzinfo=timezone.utc)
NOW_MS = NOW.timestamp() * 1000


def row(video_id='FF_1', **kw):
    base = publish.recipe_row({'video_id': video_id, 'caption': 'Hook.\n\n#formform',
                               'metadata': {'structure': 'spiral form', 'palette': 'violet-pink',
                                            'materials': ['a', 'b'], 'transformations': ['t1'],
                                            'loop_strategy': 'seamless_15s', 'sound': 'airy pad'}}, NOW)
    base.update(kw)
    return base


def tree(remote):
    return subprocess.run(['git', '-C', remote, 'ls-tree', '-r', '--name-only', 'veri'], capture_output=True,
                          text=True).stdout.split()


def show(remote, name):
    return subprocess.run(['git', '-C', remote, 'show', f'veri:{name}'], capture_output=True, text=True).stdout


class Registry(unittest.TestCase):
    def test_instagram_keeps_the_legacy_files_and_columns(self):
        ig = platforms.get('instagram')
        self.assertEqual((platforms.videos_file(ig), platforms.dna_file(ig)), ('videos.csv', 'dna_weights.csv'))
        self.assertEqual(ig.columns, VIDEO_COLUMNS)                     # panel ve eski veri bu sütunlara bağlı
        self.assertEqual(ig.id_field, 'instagram_id')
        self.assertNotIn('status', ig.columns)

    def test_other_platforms_get_their_own_folder_id_column_and_status(self):
        for name in ('youtube', 'tiktok'):
            p = platforms.get(name)
            self.assertEqual(platforms.videos_file(p), f'{name}/videos.csv')
            self.assertEqual(platforms.dna_file(p), f'{name}/dna_weights.csv')
            self.assertIn(f'{name}_id', p.columns)
            self.assertNotIn('instagram_id', p.columns)
            self.assertEqual(p.columns[-1], 'status')
            # ölçüm ve tarif sütunları aynı: scoring ve evolve her platformun kaydında aynı şekilde çalışır
            self.assertEqual([c for c in p.columns if c not in (f'{name}_id', 'status')],
                             [c for c in VIDEO_COLUMNS if c != 'instagram_id'])

    def test_unknown_platform_is_rejected(self):
        with self.assertRaises(ValueError):
            platforms.get('myspace')


class SeparateData(unittest.TestCase):
    def test_instagram_lists_are_the_instagram_ledger(self):
        remote = bare_remote()
        Store('file://' + remote).transaction(lambda d: d.videos.append({'video_id': 'A'}), 'a')
        data = Store('file://' + remote).read()
        self.assertIs(data.videos, data.ledger('instagram').videos)
        self.assertIs(data.dna, data.ledger('instagram').dna)

    def test_reading_a_platform_creates_nothing_and_commits_nothing(self):
        remote = bare_remote()
        s = Store('file://' + remote)
        s.transaction(lambda d: d.videos.append({'video_id': 'A'}), 'a')
        before = count(remote)
        s.transaction(lambda d: d.ledger('youtube').videos, 'yalnız oku')
        s.transaction(lambda d: (d.ledger('tiktok').videos, d.ledger('youtube').dna), 'yalnız oku 2')
        self.assertEqual(count(remote), before)
        self.assertEqual(sorted(tree(remote)), ['README.md', 'config.json', 'dna_weights.csv', 'videos.csv'])

    def test_a_platform_row_goes_to_its_own_file_and_leaves_instagram_bytes_alone(self):
        remote = bare_remote()
        s = Store('file://' + remote)
        s.transaction(lambda d: d.videos.append(row('FF_IG', instagram_id='M1')), 'ig')
        ig_before = show(remote, 'videos.csv')

        s.transaction(lambda d: d.ledger('youtube').videos.append(row('FF_YT', youtube_id='Y1', status='published')),
                      'yt')
        self.assertEqual(show(remote, 'videos.csv'), ig_before)           # Instagram dosyası bayt bayt aynı
        self.assertEqual(show(remote, 'dna_weights.csv').strip(), ','.join(DNA_COLUMNS))
        self.assertIn('youtube/videos.csv', tree(remote))
        yt = show(remote, 'youtube/videos.csv')
        self.assertIn('FF_YT', yt)
        self.assertNotIn('FF_IG', yt)
        self.assertEqual(yt.splitlines()[0], ','.join(platforms.get('youtube').columns))
        self.assertNotIn('tiktok/videos.csv', tree(remote))

    def test_platform_rows_roundtrip_and_stay_apart(self):
        remote = bare_remote()
        s = Store('file://' + remote)

        def write(d):
            d.videos.append(row('FF_1', instagram_id='M1'))
            d.ledger('youtube').videos.append(row('FF_1', youtube_id='Y1', status='published', yt_extra='x'))
            d.ledger('tiktok').videos.append(row('FF_1', tiktok_id='T1', status='published'))
        s.transaction(write, 'üçü')
        data = Store('file://' + remote).read()
        self.assertEqual([r['instagram_id'] for r in data.videos], ['M1'])
        self.assertEqual([r['youtube_id'] for r in data.ledger('youtube').videos], ['Y1'])
        self.assertEqual([r['tiktok_id'] for r in data.ledger('tiktok').videos], ['T1'])
        self.assertEqual(data.ledger('youtube').videos[0]['yt_extra'], 'x')       # bilinmeyen sütun kaybolmaz
        self.assertNotIn('youtube_id', data.videos[0])

    def test_git_output_language_does_not_matter(self):
        """Store git'in İngilizce hata metnini arar. Türkçe kurulu makinede (geliştirici bilgisayarı) bozulmasın."""
        seen = []
        real = subprocess.run
        remote = bare_remote()

        def spy(cmd, **kw):
            seen.append(kw.get('env', {}).get('LC_ALL'))
            return real(cmd, **kw)
        with mock.patch('ops.store.subprocess.run', spy), mock.patch.dict('os.environ', {'LC_ALL': 'tr_TR.UTF-8'}):
            Store('file://' + remote).transaction(lambda d: d.videos.append({'video_id': 'A'}), 'a')
        self.assertTrue(seen)
        self.assertEqual(set(seen), {'C'})


class Claim(unittest.TestCase):
    def test_first_claim_wins_and_records_an_inert_row(self):
        remote = bare_remote()
        s = Store('file://' + remote)
        # çağıran kimlik ve yayın zamanı vermiş olsa bile üstlenen satır boş başlar
        self.assertTrue(ledger.claim(s, 'youtube', row('FF_1', youtube_id='YANLIS')))
        rows = Store('file://' + remote).read().ledger('youtube').videos
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['video_id'], rows[0]['status'], rows[0]['youtube_id'], rows[0]['published_at']),
                         ('FF_1', 'claimed', '', ''))
        self.assertEqual(rows[0]['structure'], 'spiral form')                  # tarif (genler) yazıldı

    def test_second_claim_for_the_same_video_is_refused_without_a_new_commit(self):
        remote = bare_remote()
        s = Store('file://' + remote)
        self.assertTrue(ledger.claim(s, 'youtube', row('FF_1')))
        commits = count(remote)
        self.assertFalse(ledger.claim(Store('file://' + remote), 'youtube', row('FF_1')))
        self.assertEqual(count(remote), commits)
        self.assertEqual(len(Store('file://' + remote).read().ledger('youtube').videos), 1)

    def test_claims_are_per_platform(self):
        remote = bare_remote()
        s = Store('file://' + remote)
        self.assertTrue(ledger.claim(s, 'youtube', row('FF_1')))
        self.assertTrue(ledger.claim(s, 'tiktok', row('FF_1')))                 # öteki platformu engellemez
        data = Store('file://' + remote).read()
        self.assertEqual(ledger.taken_ids(data, 'youtube'), {'FF_1'})
        self.assertEqual(ledger.taken_ids(data, 'tiktok'), {'FF_1'})
        self.assertEqual(ledger.taken_ids(data, 'instagram'), set())

    def test_racing_claims_have_exactly_one_winner(self):
        remote = bare_remote()
        Store('file://' + remote).transaction(lambda d: d.videos.append({'video_id': 'OTHER'}), 'başlangıç')
        wins, errors = [], []
        gate = threading.Barrier(8)

        def racer():
            try:
                st = Store('file://' + remote)
                st.open()
                gate.wait()
                wins.append(ledger.claim(st, 'youtube', row('FF_RACE')))
            except Exception as e:                                          # pragma: no cover
                errors.append(e)
        threads = [threading.Thread(target=racer) for _ in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(errors, [])
        self.assertEqual(sorted(wins), [False] * 7 + [True], 'aynı video iki kez üstlenildi ya da hiç üstlenilmedi')
        self.assertEqual(len(Store('file://' + remote).read().ledger('youtube').videos), 1)

    def test_two_claimers_who_write_the_very_same_commit_still_have_one_winner(self):
        """Gerçek bir yarıştan çıkan hata: aynı saniyede aynı satırı yazan iki yarışçının commit'i bit bit aynı olursa
        ikinci itme 'zaten güncel' diye başarılı sayılır ve iki taraf da kazandım der. Burada yarış zorla kurulur:
        B, A itmeden ÖNCE senkronlanmıştır ve tüm git zamanları sabittir."""
        remote = bare_remote()
        Store('file://' + remote).transaction(lambda d: d.videos.append({'video_id': 'OTHER'}), 'başlangıç')
        a, b = Store('file://' + remote), Store('file://' + remote)
        a.open()
        b.open()                                                   # ikisi de aynı eski durumda
        calls = {'n': 0}
        real_sync = b._sync

        def stale_first():
            calls['n'] += 1
            return True if calls['n'] == 1 else real_sync()        # B'nin ilk denemesi eski veriyle çalışır
        b._sync = stale_first
        frozen = {'GIT_AUTHOR_DATE': '2026-10-02T05:00:00Z', 'GIT_COMMITTER_DATE': '2026-10-02T05:00:00Z'}
        with mock.patch.dict('os.environ', frozen):
            self.assertTrue(ledger.claim(a, 'youtube', row('FF_RACE')))
            self.assertFalse(ledger.claim(b, 'youtube', row('FF_RACE')), 'ikinci yarışçı da kazandığını sandı')
        self.assertEqual(len(Store('file://' + remote).read().ledger('youtube').videos), 1)

    def test_unwritable_store_raises_so_nothing_gets_posted(self):
        s = Store('file:///yok/boyle/bir/depo.git')
        with self.assertRaises(StoreError):
            ledger.claim(s, 'youtube', row('FF_1'))

    def test_settle_marks_published_with_id_and_time(self):
        remote = bare_remote()
        s = Store('file://' + remote)
        ledger.claim(s, 'youtube', row('FF_1'))
        ledger.settle(s, 'youtube', 'FF_1', 'YTID123', NOW)
        r = Store('file://' + remote).read().ledger('youtube').videos[0]
        self.assertEqual((r['status'], r['youtube_id'], r['published_at']),
                         ('published', 'YTID123', '2026-10-05T06:00:00.000Z'))
        self.assertEqual(len(Store('file://' + remote).read().ledger('youtube').videos), 1)     # yeni satır açmadı

    def test_settle_keeps_the_post_even_if_the_claim_row_vanished(self):
        remote = bare_remote()
        s = Store('file://' + remote)
        ledger.settle(s, 'youtube', 'FF_9', 'YT9', NOW)
        r = Store('file://' + remote).read().ledger('youtube').videos
        self.assertEqual([(x['video_id'], x['youtube_id'], x['status']) for x in r], [('FF_9', 'YT9', 'published')])

    def test_a_claimed_row_never_reaches_learning(self):
        remote = bare_remote()
        s = Store('file://' + remote)
        ledger.claim(s, 'youtube', row('FF_1'))
        rows = Store('file://' + remote).read().ledger('youtube').videos
        old = NOW_MS + 100 * 3600 * 1000
        self.assertEqual(scoring.find_due(rows, old), [])                       # yayın zamanı yok: ölçülmez
        self.assertEqual(scoring.ready_to_score(rows), [])                      # skorlanmaz
        self.assertEqual(scoring.dna_weights(rows, '2026-10-09T00:00:00.000Z'), [])      # ağırlığa girmez


def rel(i, name=None, hours_old=2, **kw):
    created = (NOW - timedelta(hours=hours_old)).strftime('%Y-%m-%dT%H:%M:%SZ')
    return release(i, name=name, created=created, **kw)


class PickForOtherPlatforms(unittest.TestCase):
    def test_instagram_picking_is_unchanged(self):
        out = publish.pick_ready([rel(1, name='published FF_X'), rel(2, name='claimed FF_Y')])
        self.assertFalse(out['ready'])                                           # adındaki durum Instagram'ınkidir
        self.assertTrue(publish.pick_ready([rel(3)])['ready'])

    def test_release_name_state_is_ignored_because_it_belongs_to_instagram(self):
        # Instagram bu videoyu çoktan 'published' yaptı: YouTube yine de görmeli
        out = publish.pick_ready([rel(1, name='published FF_20261002_060100')], taken=set())
        self.assertTrue(out['ready'])
        self.assertEqual(out['payload']['video_id'], 'FF_20261002_060100')
        out = publish.pick_ready([rel(1, name='claimed FF_20261002_060100')], taken=set())
        self.assertTrue(out['ready'])

    def test_videos_already_taken_on_this_platform_are_skipped(self):
        rels = [rel(1, hours_old=20), rel(2, hours_old=5)]                       # 2 daha yeni
        newest = publish.pick_ready(rels, taken=set())
        self.assertEqual(newest['payload']['video_id'], 'FF_20261002_060200')
        older = publish.pick_ready(rels, taken={'FF_20261002_060200'})
        self.assertEqual(older['payload']['video_id'], 'FF_20261002_060100')
        none = publish.pick_ready(rels, taken={'FF_20261002_060100', 'FF_20261002_060200'})
        self.assertFalse(none['ready'])
        self.assertIn('2 render bu platformda zaten üstlenilmiş ya da yayınlanmış', none['reason'])

    def test_stale_renders_are_not_posted(self):
        rels = [rel(1, hours_old=30)]
        out = publish.pick_ready(rels, taken=set(), now_ms=NOW_MS, max_age_hours=24)
        self.assertFalse(out['ready'])
        self.assertIn('24 saatten eski', out['reason'])
        self.assertTrue(publish.pick_ready([rel(1, hours_old=23)], taken=set(), now_ms=NOW_MS,
                                           max_age_hours=24)['ready'])
        self.assertTrue(publish.pick_ready(rels, taken=set())['ready'])          # sınır verilmezse eskilik aranmaz

    def test_age_comes_from_published_at_not_from_the_commit_date_github_calls_created_at(self):
        """Canlıdan ders: release.created_at, etiketin gösterdiği commit'in tarihidir. main'e 30 saattir commit gelmediyse
        bugün çizilmiş render da 30 saatlik görünür ve yükleme sessizce dururdu."""
        r = rel(1, hours_old=30)                                               # created_at: 30 saat önceki commit
        r['published_at'] = (NOW - timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M:%SZ')   # gerçekte 2 saat önce yayınlandı
        out = publish.pick_ready([r], taken=set(), now_ms=NOW_MS, max_age_hours=24)
        self.assertTrue(out['ready'], out.get('reason'))
        # gerçekten eskiyse (published_at 30 saat önce) yine paylaşılmaz
        r['published_at'] = (NOW - timedelta(hours=30)).strftime('%Y-%m-%dT%H:%M:%SZ')
        self.assertFalse(publish.pick_ready([r], taken=set(), now_ms=NOW_MS, max_age_hours=24)['ready'])

    def test_the_newest_is_decided_by_published_at_even_when_every_created_at_is_identical(self):
        same_commit = (NOW - timedelta(hours=26)).strftime('%Y-%m-%dT%H:%M:%SZ')
        old, new = release(1, created=same_commit), release(2, created=same_commit)
        old['published_at'] = (NOW - timedelta(hours=9)).strftime('%Y-%m-%dT%H:%M:%SZ')
        new['published_at'] = (NOW - timedelta(hours=3)).strftime('%Y-%m-%dT%H:%M:%SZ')
        for order in ([old, new], [new, old]):                                    # API sırası ne olursa olsun
            self.assertEqual(publish.pick_ready(order)['payload']['video_id'], 'FF_20261002_060200')
            self.assertEqual(publish.pick_ready([dict(r, name=r['name']) for r in order], taken=set())['payload']['video_id'],
                             'FF_20261002_060200')

    def test_without_published_at_it_falls_back_to_created_at(self):
        self.assertFalse(publish.pick_ready([rel(1, hours_old=30)], taken=set(), now_ms=NOW_MS, max_age_hours=24)['ready'])
        self.assertTrue(publish.pick_ready([rel(1, hours_old=5)], taken=set(), now_ms=NOW_MS, max_age_hours=24)['ready'])

    def test_broken_and_assetless_releases_are_still_skipped(self):
        bad = rel(1)
        bad['body'] = 'bozuk{'
        self.assertFalse(publish.pick_ready([bad, rel(2, assets=False)], taken=set())['ready'])
        self.assertEqual(publish.pick_ready([bad, rel(3)], taken=set())['payload']['video_id'], 'FF_20261002_060300')


class Dispatch(unittest.TestCase):
    def test_a_platform_without_a_publisher_fails_without_touching_anything(self):
        with World([release(1)]) as w:
            # Üç platformun da yayıncısı var: koruma, yayıncısı olmayan bir platform için geçici kaldırılarak sınanır
            with mock.patch.dict(publish.PUBLISHERS, {}, clear=True):
                for name in ('instagram', 'youtube', 'tiktok'):
                    with self.assertRaises(OpsError) as cm:
                        publish.run(w.store(), platform=name, log=lambda *_: None)
                    self.assertIn('henüz kurulu değil', str(cm.exception))
                    self.assertIn('Hiçbir şey paylaşılmadı', str(cm.exception))
            self.assertEqual(w.log(), [])                                        # ağ yok: ne GitHub ne platform
            self.assertEqual(tree(w.remote), [])                                 # veri dalı da yok

    def test_unknown_platform_is_a_clear_error(self):
        with World([release(1)]) as w:
            with self.assertRaises(OpsError) as cm:
                publish.run(w.store(), platform='myspace')
            self.assertIn('bilinmeyen platform', str(cm.exception))

    def test_instagram_publish_is_identical_when_the_platform_is_named_and_leaves_other_ledgers_empty(self):
        with World([release(1)]) as w:
            out = publish.run(w.store(), platform='instagram', clock=lambda: NOW, sleep=lambda s: None,
                              log=lambda *_: None, workdir=tempfile.mkdtemp())
            self.assertEqual(out, {'video_id': 'FF_20261002_060100', 'instagram_id': 'MEDIA777'})
            self.assertEqual(w.releases[501]['name'], 'published FF_20261002_060100')
            self.assertEqual(sorted(tree(w.remote)), ['README.md', 'config.json', 'dna_weights.csv', 'videos.csv'])
            row_ = w.store().read().videos[0]
            self.assertEqual((row_['instagram_id'], row_['published_at']), ('MEDIA777', '2026-10-05T06:00:00.000Z'))

    def test_the_command_line_accepts_a_platform_and_defaults_to_instagram(self):
        parser = cli.build_parser()
        self.assertEqual(parser.parse_args(['publish']).platform, 'instagram')
        self.assertEqual(parser.parse_args(['publish', '--platform', 'youtube', '--dry-run']).platform, 'youtube')
        with self.assertRaises(SystemExit):
            parser.parse_args(['publish', '--platform', 'myspace'])


if __name__ == '__main__':
    unittest.main()
