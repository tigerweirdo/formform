"""Nöbetçi: saatin kaçırdığı turu başlatır, saatin yaptığı işe dokunmaz.

Workflow başına iki yedek cron'un yerini aldı (boş turlar dakika harcıyordu). Asıl güvenceler: yolunda giden günde
hiçbir şey başlatmaz, aynı turu iki kez açmaz, hazır video olmadan yayın başlatmaz, saat çalışmıyorsa bunu söyler."""
import unittest
from datetime import datetime, timedelta, timezone

from ops import alerts, schedule, watch
from ops.errors import ClockMissed, OpsError
from ops.tests.fakes import World
from ops.tests.test_alerts_cli import cli_run

# 7 Ekim 2026, 09:25 Berlin (yaz): sabah turlarının hepsi geçmiş. render 06:30, publish 07:45, tiktok 07:50, learn 06:05 UTC
NOW = datetime(2026, 10, 7, 7, 25, tzinfo=timezone.utc)
SLOTS = {'render': '2026-10-07T04:30:00Z', 'publish': '2026-10-07T05:45:00Z', 'tiktok': '2026-10-07T05:50:00Z',
         'learn': '2026-10-07T06:05:00Z'}


def at(text, minutes=0):
    return (datetime.fromisoformat(text.replace('Z', '+00:00')) + timedelta(minutes=minutes)).strftime('%Y-%m-%dT%H:%M:%SZ')


def ran(job, conclusion='success', minutes=1, title='saat'):
    """O işin sabah yuvasında açılmış çalışma."""
    return {'created_at': at(SLOTS[job], minutes), 'status': 'completed' if conclusion else 'in_progress',
            'conclusion': conclusion, 'display_title': f'{job} ({title})'}


def healthy(w, *missing):
    for job in schedule.WATCHED:
        w.wf_runs[f'{job}.yml'] = [] if job in missing else [ran(job)]


def started(w):
    return [name for name, _ in w.dispatched]


def go(**kw):
    lines = []
    return watch.run(now=kw.pop('now', NOW), log=lines.append, **kw), lines


class Slots(unittest.TestCase):
    def test_the_latest_slot_follows_berlin_time_in_both_seasons(self):
        winter = datetime(2026, 12, 8, 7, 25, tzinfo=timezone.utc)                     # 08:25 Berlin (kış)
        want = {'render': ('2026-10-07T04:30', '2026-12-08T05:30'), 'publish': ('2026-10-07T05:45', '2026-12-08T06:45'),
                'tiktok': ('2026-10-07T05:50', '2026-12-08T06:50'), 'learn': ('2026-10-07T06:05', '2026-12-08T06:05')}
        for job, (summer_slot, winter_slot) in want.items():
            self.assertEqual(schedule.latest_slot(job, NOW).strftime('%Y-%m-%dT%H:%M'), summer_slot, job)
            self.assertEqual(schedule.latest_slot(job, winter).strftime('%Y-%m-%dT%H:%M'), winter_slot, job)

    def test_a_slot_is_not_due_until_the_clocks_own_window_has_closed(self):
        early = datetime(2026, 10, 7, 4, 55, tzinfo=timezone.utc)                      # render yuvasından 25 dk sonra
        self.assertEqual(schedule.latest_slot('render', early).strftime('%d %H:%M'), '06 16:30')   # hâlâ dünkü yuva
        self.assertEqual(schedule.latest_slot('render', early + timedelta(minutes=5)).strftime('%d %H:%M'), '07 04:30')
        self.assertGreater(schedule.WATCH_GRACE_MIN, 20, 'saatin 20 dakikalık penceresinden uzun olmalı')

    def test_any_run_since_the_slot_that_did_not_fail_covers_it(self):
        slot = schedule.latest_slot('publish', NOW)
        self.assertTrue(schedule.covered([ran('publish')], slot))
        self.assertTrue(schedule.covered([ran('publish', None)], slot))                 # hâlâ sürüyor
        self.assertTrue(schedule.covered([ran('publish', title='elle')], slot))         # elle çalıştırılmış: iş yapılmış
        self.assertTrue(schedule.covered([ran('publish', title='nöbet')], slot))
        for bad in ('failure', 'cancelled', 'timed_out', 'startup_failure'):
            self.assertFalse(schedule.covered([ran('publish', bad)], slot), bad)
        self.assertFalse(schedule.covered([ran('publish', minutes=-30)], slot))          # yuvadan önce: önceki turun işi
        self.assertFalse(schedule.covered([], slot))

    def test_the_watchman_runs_after_every_slot_it_guards_in_both_seasons(self):
        cron = schedule.cron_strings('watch')[0]
        for day in (datetime(2026, 7, 1, tzinfo=timezone.utc), datetime(2026, 12, 8, tzinfo=timezone.utc)):
            for hour in (7, 19):
                fire = day.replace(hour=hour, minute=25)
                self.assertTrue(schedule.matches_cron(cron, fire))
                for job in ('render', 'publish', 'tiktok'):
                    age = fire - schedule.latest_slot(job, fire)
                    self.assertLess(age, timedelta(hours=schedule.WATCH_MAX_AGE_H), f'{job} {fire}')


class Watch(unittest.TestCase):
    def test_a_normal_day_starts_nothing_and_passes(self):
        with World() as w:
            healthy(w)
            out, lines = go()
            self.assertEqual(started(w), [])
            self.assertEqual(out['started'], [])
            self.assertEqual(lines, ['Nöbet: bütün turlar zamanında başlamış.'])

    def test_a_missed_publish_is_started_with_the_watch_flag_on_main_and_reported(self):
        with World() as w:
            healthy(w, 'publish')
            with self.assertRaises(ClockMissed) as ctx:
                go()
            self.assertEqual(w.dispatched, [('publish.yml', {'ref': 'main', 'inputs': {'watch': 'true'}})])
            self.assertIn('Saat şu turları başlatmadı: publish 07:45 (hiç başlamadı)', str(ctx.exception))
            self.assertIn('DISPATCH_TOKEN', ctx.exception.hint)

    def test_when_the_clock_is_down_only_the_render_starts_and_publishing_waits_for_the_video(self):
        with World() as w:
            healthy(w, 'render', 'publish', 'tiktok', 'learn')
            with self.assertRaises(ClockMissed) as ctx:
                go()
            self.assertEqual(started(w), ['render.yml', 'learn.yml'], 'hazır video olmadan yayın başlatıldı')
            text = str(ctx.exception)
            for part in ('render 06:30 (hiç başlamadı)', 'publish 07:45', 'tiktok 07:50', 'learn 08:05',
                         'Nöbetçi başlattı: render 06:30 (hiç başlamadı), learn 08:05 (hiç başlamadı).',
                         'Render bitince başlayacak: publish 07:45 (hiç başlamadı), tiktok 07:50 (hiç başlamadı).'):
                self.assertIn(part, text)

    def test_the_render_it_started_then_starts_the_missed_publish_and_tiktok(self):
        with World() as w:
            healthy(w, 'publish', 'tiktok', 'learn')                           # render bitti, öğrenme başka konu
            later = NOW + timedelta(minutes=25)
            out, lines = go(now=later, after_render=True)                      # render turunun son adımı
            self.assertEqual(started(w), ['publish.yml', 'tiktok.yml'])
            self.assertEqual(len(out['started']), 2)
            self.assertNotIn('learn.yml', started(w))

    def test_after_a_render_nothing_is_started_twice_and_nothing_is_reported(self):
        with World() as w:
            healthy(w, 'tiktok')                                               # yayın saatinde yapılmış, yalnız TikTok kaçmış
            out, _ = go(after_render=True)
            self.assertEqual(started(w), ['tiktok.yml'])
            w.dispatched.clear()
            healthy(w)
            out, _ = go(after_render=True)                                     # hata vermez, iş yoksa hiçbir şey açmaz
            self.assertEqual((started(w), out['started']), ([], []))

    def test_a_failed_run_is_tried_again_without_blaming_the_clock(self):
        with World() as w:
            healthy(w)
            w.wf_runs['publish.yml'] = [ran('publish', 'failure')]
            out, lines = go()                                                  # hata vermez: publish kendi bildirimini açtı
            self.assertEqual(started(w), ['publish.yml'])
            self.assertEqual(out['never'], [])
            self.assertIn('Başlatıldı: publish 07:45 (başarısız oldu)', lines)

    def test_a_second_watch_does_not_start_the_same_run_again(self):
        with World() as w:
            healthy(w, 'publish')
            with self.assertRaises(ClockMissed):
                go()
            out, _ = go(now=NOW + timedelta(minutes=1))                        # başlattığı tur yuvayı doldurdu
            self.assertEqual(started(w), ['publish.yml'])
            self.assertEqual(out['started'], [])

    def test_a_run_in_progress_or_started_by_hand_counts(self):
        with World() as w:
            healthy(w)
            w.wf_runs['render.yml'] = [ran('render', None)]                    # 30 sn'lik video hâlâ çiziliyor
            w.wf_runs['publish.yml'] = [ran('publish', title='elle', minutes=40)]
            go()
            self.assertEqual(started(w), [])

    def test_a_slot_too_old_is_left_to_the_next_one(self):
        with World() as w:
            healthy(w, 'render', 'publish', 'tiktok')
            late = datetime(2026, 10, 7, 13, 0, tzinfo=timezone.utc)           # GitHub nöbetçiyi 15:00'e geciktirdi (8,5 saat)
            w.wf_runs['learn.yml'] = [{'created_at': '2026-10-07T12:06:00Z', 'conclusion': 'success'}]
            with self.assertRaises(ClockMissed) as ctx:                        # başlatmaz ama saatin kaçırdığını söyler
                go(now=late)
            self.assertEqual(started(w), [], 'akşam yuvası 3,5 saat sonra: sabahın turu şimdi başlatılmaz')
            self.assertIn('Çok eski olduğu için başlatılmadı: render 06:30 (hiç başlamadı), publish 07:45', str(ctx.exception))

    def test_publishing_is_not_started_for_a_cycle_whose_render_is_too_old_to_start(self):
        with World() as w:
            healthy(w, 'render', 'publish', 'tiktok')
            w.wf_runs['learn.yml'] = [{'created_at': '2026-10-07T12:06:00Z', 'conclusion': 'success'}]
            late = datetime(2026, 10, 7, 12, 45, tzinfo=timezone.utc)          # render yuvası 8 sa 15 dk, yayın yuvası 7 saat önce
            with self.assertRaises(ClockMissed):
                go(now=late)
            self.assertEqual(started(w), [], 'render başlatılmayacaksa yayın da başlatılmaz: hazır video olmaz')

    def test_a_slot_whose_window_is_still_open_is_left_to_the_clock(self):
        with World() as w:
            healthy(w)
            w.wf_runs['learn.yml'] = [{'created_at': '2026-10-07T03:06:00Z', 'conclusion': 'success'}]
            early = datetime(2026, 10, 7, 6, 20, tzinfo=timezone.utc)          # learn 08:05 yuvasından 15 dk sonra
            go(now=early)
            self.assertEqual(started(w), [])

    def test_when_runs_cannot_be_read_nothing_is_started(self):
        with World() as w:
            w.runs_status = 500
            with self.assertRaises(OpsError) as ctx:
                go()
            self.assertNotIsInstance(ctx.exception, ClockMissed)
            self.assertIn('okuyamadı', str(ctx.exception))
            self.assertEqual(w.dispatched, [])

    def test_a_dispatch_that_fails_is_reported_and_not_retried(self):
        with World() as w:
            healthy(w, 'publish')
            w.dispatch_status = 422
            with self.assertRaises(OpsError) as ctx:
                go()
            self.assertIn('publish 07:45', str(ctx.exception))
            self.assertEqual(len(w.srv.named('POST', r'/gh/repos/o/r/actions/workflows/publish.yml/dispatches')), 1)


class CommandLine(unittest.TestCase):
    def test_a_clean_watch_exits_zero(self):
        with World() as w:
            # Komut gerçek saati kullanır: çalışmalar da gerçek saate göre en son yuvalarında açılmış olmalı.
            # (Sabit 7 Ekim sabahı çalışmalarıyla test, o günün sonraki yuvası geçince kırılıyordu.)
            now = datetime.now(timezone.utc)
            for job in schedule.WATCHED:
                opened = schedule.latest_slot(job, now) + timedelta(minutes=1)
                w.wf_runs[f'{job}.yml'] = [dict(ran(job), created_at=opened.strftime('%Y-%m-%dT%H:%M:%SZ'))]
            code, out = cli_run('watch')
            self.assertEqual(code, 0, out)

    def test_a_missed_run_fails_the_watch_with_a_hint_about_the_clock(self):
        with World() as w:
            # gerçek saat kullanılır: bütün yuvalar boş, en az öğrenme (üç saatte bir) kaçmış görünür
            code, out = cli_run('watch')
            self.assertEqual(code, 1)
            self.assertIn('::error::Saat şu turları başlatmadı', out)
            self.assertTrue(w.dispatched)
            self.assertEqual(alerts.report_failure('FORMFORM watch', 'https://x/run/9'), 'created')
            self.assertIn('DISPATCH_TOKEN', w.issues[0]['body'])

    def test_after_render_never_fails_the_render(self):
        with World() as w:
            code, out = cli_run('watch', '--after-render')
            self.assertEqual(code, 0, out)
            self.assertLessEqual(set(started(w)), {'publish.yml', 'tiktok.yml'})


if __name__ == '__main__':
    unittest.main()
