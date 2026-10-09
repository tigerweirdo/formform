"""Berlin duvar saati: yaz ve kış cron’larından yalnız biri iş yapar."""
import os
import unittest
from datetime import datetime, timezone

from ops import report, schedule


def utc(*parts):
    return datetime(*parts, tzinfo=timezone.utc)


class BerlinOffset(unittest.TestCase):
    def test_summer_and_winter_offsets(self):
        self.assertEqual(schedule.offset_hours(utc(2026, 10, 3, 12)), 2)     # CEST
        self.assertEqual(schedule.offset_hours(utc(2026, 1, 15, 12)), 1)      # CET
        self.assertEqual(schedule.offset_hours(utc(2026, 10, 25, 0, 30)), 2)  # geçişten önce
        self.assertEqual(schedule.offset_hours(utc(2026, 10, 25, 2)), 1)      # geçişten sonra

    def test_only_the_current_season_runs(self):
        summer = utc(2026, 10, 3, 4, 30)
        winter = utc(2026, 1, 15, 5, 30)
        self.assertTrue(schedule.should_run('render', 'schedule', '30 4,16 * * *', summer))
        self.assertFalse(schedule.should_run('render', 'schedule', '30 5,17 * * *', summer))
        self.assertTrue(schedule.should_run('render', 'schedule', '30 5,17 * * *', winter))
        self.assertFalse(schedule.should_run('render', 'schedule', '30 4,16 * * *', winter))

    def test_manual_runs_and_learn_ignores_season(self):
        now = utc(2026, 1, 15, 5, 30)
        self.assertTrue(schedule.should_run('publish', 'workflow_dispatch', '', now))
        self.assertTrue(schedule.should_run('learn', 'schedule', '5 */3 * * *', now))

    def test_wall_clock_is_the_same_in_both_seasons(self):
        self.assertEqual(schedule.local_times('render'), ['06:30', '18:30'])
        self.assertEqual(schedule.local_times('publish'), ['07:45', '19:45'])
        self.assertEqual(schedule.local_times('token'), ['05:07'])
        self.assertEqual(schedule.local_times('report'), ['09:17'])

    def test_report_splits_morning_and_evening_in_berlin(self):
        # 12:00 UTC kışın Berlin’de 13:00 (sabah), İstanbul’da 15:00 (akşam) olurdu.
        self.assertEqual(report._hour_of('2026-01-15T12:00:00Z'), 13)
        self.assertLess(report._hour_of('2026-01-15T12:00:00Z'), 14)
        self.assertEqual(report._hour_of('2026-07-15T05:00:00Z'), 7)    # 07:00 yaz
        self.assertEqual(report._hour_of('2026-07-15T17:00:00Z'), 19)   # 19:00 yaz
        self.assertGreaterEqual(report._hour_of('2026-07-15T17:00:00Z'), 14)


class ClockSlot(unittest.TestCase):
    def test_latest_occurrence_is_the_morning_slot_before_a_late_run(self):
        got = schedule.latest_occurrence('30 4,16 * * *', utc(2026, 10, 4, 10, 42))
        self.assertEqual(got, utc(2026, 10, 4, 4, 30))

    def test_clock_run_covers_success_and_in_progress_not_failure(self):
        slot = utc(2026, 10, 4, 4, 30)
        def run(at, conclusion, title='render (saat)'):
            return {'displayTitle': title, 'createdAt': at, 'conclusion': conclusion}
        self.assertTrue(schedule.clock_run_covers([run('2026-10-04T04:31:00Z', 'success')], slot))
        self.assertTrue(schedule.clock_run_covers([run('2026-10-04T04:31:00Z', None)], slot))
        self.assertFalse(schedule.clock_run_covers([run('2026-10-04T04:31:00Z', 'failure')], slot))
        self.assertFalse(schedule.clock_run_covers([run('2026-10-04T04:27:00Z', 'success')], slot))
        self.assertFalse(schedule.clock_run_covers([run('2026-10-04T04:31:00Z', 'success', 'render (yaz saati)')], slot))
        self.assertFalse(schedule.clock_run_covers([], None))

    def test_tiktok_fires_at_07_50_and_19_50_berlin_in_both_seasons_and_only_the_current_seasons_cron_runs(self):
        from datetime import datetime, timezone
        summer = datetime(2026, 10, 6, 5, 50, tzinfo=timezone.utc)       # Berlin UTC+2 -> 07:50
        winter = datetime(2026, 12, 8, 6, 50, tzinfo=timezone.utc)       # Berlin UTC+1 -> 07:50
        summer_cron, winter_cron = '50 5,17 * * *', '50 6,18 * * *'
        self.assertTrue(schedule.should_run('tiktok', 'schedule', summer_cron, summer))
        self.assertFalse(schedule.should_run('tiktok', 'schedule', winter_cron, summer))   # diğer mevsimin cron'u iş yapmaz
        self.assertTrue(schedule.should_run('tiktok', 'schedule', winter_cron, winter))
        self.assertFalse(schedule.should_run('tiktok', 'schedule', summer_cron, winter))
        self.assertTrue(schedule.should_run('tiktok', 'workflow_dispatch', '', summer))     # elle her zaman
        # Instagram'dan 5 dk sonra: ~08:00 / ~20:00'de paylaşılırken taslak da gelen kutusundadır
        self.assertEqual(schedule.local_times('tiktok'), ['07:50', '19:50'])
        self.assertEqual(schedule.local_times('publish'), ['07:45', '19:45'])

    def test_the_worker_clock_starts_tiktok_at_its_slot_and_not_at_instagrams(self):
        import json
        import shutil
        import subprocess
        if not shutil.which('node'):
            self.skipTest('Node yok')
        script = r"""
import { dueJobs } from './dashboard/clock.js';
const keys = (iso) => dueJobs(new Date(iso)).map((j) => j.key).sort();
console.log(JSON.stringify({ morning: keys('2026-10-06T05:50:00Z'), evening: keys('2026-10-06T17:55:00Z'),
  instagramSlot: keys('2026-10-06T05:45:00Z'), later: keys('2026-10-06T06:15:00Z') }));
"""
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        p = subprocess.run(['node', '--input-type=module', '-e', script], cwd=root, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        got = json.loads(p.stdout)
        # Worker her işi hedef saatten sonraki 20 dk'lık pencerede 'vadesi geldi' sayar (kaçan turu yakalamak için):
        # 07:50'de Instagram'ın penceresi de açıktır, aynı yuvanın tekrar başlatılmasını alreadyCovered engeller
        self.assertIn('tiktok', got['morning'])
        self.assertIn('tiktok', got['evening'])
        self.assertEqual(got['instagramSlot'], ['publish'])       # 07:45'te TikTok'un yuvası henüz açılmadı
        self.assertNotIn('tiktok', got['later'])                  # pencere kapanınca (08:10'dan sonra) tekrar başlatmaz

    def test_worker_due_jobs_follow_berlin_and_the_learn_hour(self):
        import json
        import shutil
        import subprocess
        if not shutil.which('node'):
            self.skipTest('Node yok')
        script = r"""
import { dueJobs, alreadyCovered, JOBS } from './dashboard/clock.js';
const keys = (iso) => dueJobs(new Date(iso)).map((j) => j.key).sort();
const covered = alreadyCovered(
  [{ created_at: '2026-10-04T04:31:00Z', conclusion: 'success', display_title: 'render (yaz saati)' }],
  Date.parse('2026-10-04T04:30:00Z'));
const failed = alreadyCovered(
  [{ created_at: '2026-10-04T04:31:00Z', conclusion: 'failure', display_title: 'render (saat)' }],
  Date.parse('2026-10-04T04:30:00Z'));
console.log(JSON.stringify({
  on: keys('2026-10-04T04:30:00Z'),
  still: keys('2026-10-04T04:46:00Z'),
  off: keys('2026-10-04T04:50:00Z'),
  publish: keys('2026-10-04T05:45:00Z'),
  token: keys('2026-10-01T03:07:00Z'),
  tokenOff: keys('2026-10-04T03:07:00Z'),
  report: keys('2026-10-05T07:17:00Z'),
  learn: keys('2026-10-04T00:05:00Z'),
  learnOff: keys('2026-10-04T01:05:00Z'),
  locals: JOBS.filter((j) => j.times).map((j) => [j.key, j.times]),
  covered, failed
}));
"""
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        p = subprocess.run(['node', '--input-type=module', '-e', script], cwd=root, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        got = json.loads(p.stdout)
        self.assertEqual(got['on'], ['render'])
        self.assertEqual(got['still'], ['render'])
        self.assertNotIn('render', got['off'])
        self.assertEqual(got['publish'], ['publish'])
        self.assertIn('token', got['token'])
        self.assertIn('learn', got['token'])
        self.assertNotIn('token', got['tokenOff'])
        self.assertEqual(got['report'], ['report'])
        self.assertEqual(got['learn'], ['learn'])
        self.assertEqual(got['learnOff'], [])
        self.assertTrue(got['covered'])
        self.assertFalse(got['failed'])
        locals_ = {key: times for key, times in got['locals']}
        for job, times in locals_.items():
            self.assertEqual(times, schedule.local_times(job), job)


if __name__ == '__main__':
    unittest.main()
