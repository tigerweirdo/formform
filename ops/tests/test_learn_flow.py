"""Öğrenme akışı: sahte Instagram üzerinde ölçüm → skor → ağırlık."""
import subprocess
import unittest
from datetime import datetime, timedelta, timezone

from ops import learn
from ops.errors import OpsError, TokenMissing
from ops.tests.fakes import World
from ops.store import VIDEO_COLUMNS

NOW = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)
VIEWS = 'views,likes,comments,shares,saved'
WATCH = 'ig_reels_avg_watch_time'


def video(i, hours_ago, **kw):
    pub = (NOW - timedelta(hours=hours_ago)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
    row = {c: '' for c in VIDEO_COLUMNS}
    row.update(video_id=f'FF_{i}', instagram_id=f'M{i}', published_at=pub, structure='spiral form',
               palette='violet-pink', motion_quality='m', lighting='l', loop_strategy='seamless_8s',
               materials='a, b', transformations='t1, t2', caption='Hook A.\n\nx', selection_mode='weights')
    row.update(kw)
    return row


def insight(views, likes=0, comments=0, shares=0, saves=0):
    return 200, {'data': [{'name': 'views', 'values': [{'value': views}]}, {'name': 'likes', 'values': [{'value': likes}]},
                          {'name': 'comments', 'values': [{'value': comments}]},
                          {'name': 'shares', 'values': [{'value': shares}]},
                          {'name': 'saved', 'values': [{'value': saves}]}]}


def watch(ms):
    return 200, {'data': [{'name': WATCH, 'values': [{'value': ms}]}]}


def seed(world, rows):
    world.store().transaction(lambda d: d.videos.extend(rows), 'tohum')


def commits(world):
    return int(subprocess.run(['git', '-C', world.remote, 'rev-list', '--count', 'veri'], capture_output=True,
                              text=True).stdout or 0)


def run(world):
    return learn.run(world.store(), clock=lambda: NOW, log=lambda *_: None)


class LearnFlow(unittest.TestCase):
    def test_measures_scores_and_learns(self):
        with World() as w:
            seed(w, [video(1, 80), video(2, 30), video(3, 5), video(9, 100, video_id='TEST_9')])
            w.insights[('M1', VIEWS)] = insight(1000, likes=100, comments=10, shares=20, saves=30)
            w.insights[('M1', WATCH)] = watch(6543)
            w.insights[('M2', VIEWS)] = insight(300, likes=10)
            w.insights[('M2', WATCH)] = (200, {'data': []})
            out = run(w)
            rows = {r['video_id']: r for r in w.store().read().videos}
            self.assertEqual((rows['FF_1']['views_72h'], rows['FF_1']['likes'], rows['FF_1']['saves']),
                             ('1000', '100', '30'))
            self.assertEqual(rows['FF_1']['avg_watch_time'], '6.54')           # 6543 ms → sn
            self.assertEqual(rows['FF_2']['views_24h'], '300')                 # 30 saat: yalnız 24h penceresi
            self.assertEqual(rows['FF_2']['views_72h'], '')
            self.assertEqual(rows['FF_3']['views_24h'], '')                    # 5 saat: henüz erken
            self.assertEqual(rows['TEST_9']['views_72h'], '')                  # TEST_ satırına dokunulmaz
            self.assertNotEqual(rows['FF_1']['performance_score'], '')
            self.assertEqual(rows['FF_2']['performance_score'], '')            # 72 saatlik veri yok
            self.assertEqual(out['measured'], 2)
            self.assertEqual(out['scored'], 1)
            dna = {r['key']: r for r in w.store().read().dna}
            self.assertIn('structure::spiral form', dna)
            self.assertEqual(dna['structure::spiral form']['sample_count'], '1')
            self.assertIn('hook::Hook A.', dna)
            self.assertNotIn('hook::', dna)

    def test_sound_and_hashtags_are_learned_through_the_whole_flow(self):
        with World() as w:
            seed(w, [video(1, 80, sound='deep drone', caption='Hook A.\n\nx\n\nform/form — drift.\n\n#formform #lightart #hypnotic')])
            w.insights[('M1', VIEWS)] = insight(700, likes=40)
            w.insights[('M1', WATCH)] = watch(5000)
            run(w)
            dna = {r['key']: r for r in w.store().read().dna}
            self.assertEqual(dna['sound::deep drone']['sample_count'], '1')
            self.assertIn('tag::#lightart', dna)
            self.assertIn('tag::#hypnotic', dna)

    def test_second_run_does_nothing_new(self):
        with World() as w:
            seed(w, [video(1, 80)])
            w.insights[('M1', VIEWS)] = insight(500, likes=5)
            w.insights[('M1', WATCH)] = watch(4000)
            run(w)
            run(w)                                  # 24h penceresi ikinci turda dolar
            before = commits(w)
            run(w)                                  # artık yapacak iş yok
            self.assertEqual(commits(w), before, 'değişiklik yokken yeni kayıt işlendi')
            self.assertEqual(w.store().read().videos[0]['views_24h'], '500')

    def test_instagram_errors_leave_rows_blank_but_weights_still_computed(self):
        with World() as w:
            scored = video(1, 200, views_72h='400', views_24h='200', likes='10', performance_score='55')
            seed(w, [scored, video(2, 80)])
            with self.assertRaises(OpsError) as cm:                # hepsi başarısız: kırmızı biter
                run(w)
            self.assertIn('Instagram ölçüm çağrıları başarısız', str(cm.exception))
            data = w.store().read()
            row = next(r for r in data.videos if r['video_id'] == 'FF_2')
            self.assertEqual((row['views_72h'], row['likes']), ('', ''))      # sıfır yazılmadı
            self.assertTrue(any(d['key'] == 'structure::spiral form' for d in data.dna))

    def test_partial_failure_is_not_an_error(self):
        with World() as w:
            seed(w, [video(1, 80), video(2, 80)])
            w.insights[('M1', VIEWS)] = insight(50)
            run(w)                                                          # M2 hata verdi, M1 yazıldı
            rows = {r['video_id']: r for r in w.store().read().videos}
            self.assertEqual((rows['FF_1']['views_72h'], rows['FF_2']['views_72h']), ('50', ''))

    def test_missing_token_still_scores_and_learns_then_fails(self):
        with World(env={'IG_ACCESS_TOKEN': ''}) as w:
            seed(w, [video(1, 200, views_72h='100', views_24h='60', likes='5'), video(2, 80)])
            with self.assertRaises(TokenMissing):
                run(w)
            data = w.store().read()
            self.assertNotEqual(next(r for r in data.videos if r['video_id'] == 'FF_1')['performance_score'], '')
            self.assertTrue(data.dna)

    def test_nothing_due_and_no_token_is_fine(self):
        with World(env={'IG_ACCESS_TOKEN': ''}) as w:
            seed(w, [video(1, 3)])
            self.assertEqual(run(w)['measured'], 0)

    def test_empty_store_is_fine(self):
        with World() as w:
            self.assertEqual(run(w), {'measured': 0, 'scored': 0, 'weights': 0})

    def test_does_not_overwrite_concurrent_additions(self):
        """Ölçüm sırasında (ağ çağrıları) başka bir akış satır eklese bile satır kaybolmaz."""
        with World() as w:
            seed(w, [video(1, 80)])
            w.insights[('M1', VIEWS)] = insight(70)
            def slow_and_add(c, m):
                w.store().transaction(lambda d: d.videos.append(video(7, 1)), 'yarışan yayın')
                return w.insights[('M1', VIEWS)]
            w.srv.route('GET', r'/ig/v23.0/(M1)/insights',
                        lambda c, m: slow_and_add(c, m) if c.query.get('metric') == VIEWS else (200, {'data': []}))
            run(w)
            ids = sorted(r['video_id'] for r in w.store().read().videos)
            self.assertEqual(ids, ['FF_1', 'FF_7'])


if __name__ == '__main__':
    unittest.main()
