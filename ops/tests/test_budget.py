"""Ücretsiz dakika bütçesi: karar mantığı, kayıt, bir aylık simülasyon, komut satırı, rapor."""
import json
import os
import random
import tempfile
import time
import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone

from ops import budget, cli, evolve, report
from ops.tests.fakes import World
from ops.tests.test_alerts_cli import cli_run

OCT = datetime(2026, 10, 3, 2, 30, tzinfo=timezone.utc)


def used(minutes, by_format=None, month='2026-10'):
    return {'usage': {month: {'render_min': minutes, 'renders': 5, 'by_format': by_format or {}}}}


class Calendar(unittest.TestCase):
    def test_month_is_the_berlin_month(self):
        # 30 Eylül 22:00 UTC = 1 Ekim 00:00 Berlin (yaz, UTC+2)
        self.assertEqual(budget.month_key(datetime(2026, 9, 30, 22, 0, tzinfo=timezone.utc)), '2026-10')
        # 31 Ekim 2026 kış saati (UTC+1): 22:59 UTC hâlâ ekim, 23:00 UTC 1 Kasım
        self.assertEqual(budget.month_key(datetime(2026, 10, 31, 22, 59, tzinfo=timezone.utc)), '2026-10')
        self.assertEqual(budget.month_key(datetime(2026, 10, 31, 23, 0, tzinfo=timezone.utc)), '2026-11')

    def test_days_left_includes_today(self):
        self.assertEqual(budget.days_left(OCT), 29)
        self.assertEqual(budget.days_left(datetime(2026, 10, 31, 12, tzinfo=timezone.utc)), 1)
        self.assertEqual(budget.days_left(datetime(2026, 2, 1, 12, tzinfo=timezone.utc)), 28)


class Decisions(unittest.TestCase):
    def test_fresh_month_allows_everything_in_full_quality(self):
        d = budget.decide({}, OCT, evolve.FORMAT_NAMES)
        self.assertEqual((d['level'], d['formats'], (d['width'], d['height'])),
                         ('full', evolve.FORMAT_NAMES, (1080, 1920)))

    def test_heavy_use_drops_the_longest_format_first_then_the_middle_one(self):
        mid = budget.decide(used(900), datetime(2026, 10, 15, 2, 30, tzinfo=timezone.utc), evolve.FORMAT_NAMES)
        self.assertEqual(mid['formats'], ['seamless_8s', 'seamless_15s'])
        late = budget.decide(used(1350), datetime(2026, 10, 25, 2, 30, tzinfo=timezone.utc), evolve.FORMAT_NAMES)
        self.assertEqual(late['formats'], ['seamless_8s'])
        self.assertEqual(late['level'], 'full')

    def test_exhausted_budget_falls_back_to_economy_instead_of_stopping(self):
        d = budget.decide(used(1600), datetime(2026, 10, 20, 2, 30, tzinfo=timezone.utc), evolve.FORMAT_NAMES)
        self.assertEqual((d['level'], d['formats'], (d['width'], d['height'])), ('lite', ['seamless_8s'], (720, 1280)))
        self.assertEqual(d['remaining'], 0.0)

    def test_always_returns_at_least_one_format(self):
        for minutes in (0, 500, 1499, 1500, 3000):
            for day in (1, 10, 31):
                d = budget.decide(used(minutes), datetime(2026, 10, day, 12, tzinfo=timezone.utc), evolve.FORMAT_NAMES)
                self.assertTrue(d['formats'])
                self.assertTrue(set(d['formats']) <= set(evolve.FORMAT_NAMES))

    def test_usage_resets_with_the_new_month(self):
        d = budget.decide(used(1600, month='2026-09'), OCT, evolve.FORMAT_NAMES)
        self.assertEqual(d['level'], 'full')
        self.assertEqual(d['used'], 0.0)

    def test_measured_cost_replaces_the_estimate_after_two_samples(self):
        cfg = used(100, {'seamless_8s': {'n': 1, 'min': 40.0}})
        self.assertEqual(budget.cost(budget.usage_for(cfg, OCT), 'seamless_8s'), budget.DEFAULT_COST['seamless_8s'])
        cfg = used(1470, {'seamless_8s': {'n': 2, 'min': 60.0}})
        self.assertEqual(budget.cost(budget.usage_for(cfg, OCT), 'seamless_8s'), 30.0)
        # Ay sonunda 30 dk kalmışken 8 sn gerçekte 30 dk çıkıyorsa (tahmin 11 dk idi) ekonomik moda düşer
        late = budget.decide(cfg, datetime(2026, 10, 28, 2, 30, tzinfo=timezone.utc), evolve.FORMAT_NAMES)
        self.assertEqual(late['level'], 'lite')
        # aynı kalan bütçede ölçüm tahminle uyuşsaydı 8 sn hâlâ tam kalitede yapılırdı
        fine = used(1470, {'seamless_8s': {'n': 2, 'min': 8.0}})
        self.assertEqual(budget.decide(fine, datetime(2026, 10, 28, 2, 30, tzinfo=timezone.utc),
                                       evolve.FORMAT_NAMES)['level'], 'full')


class Recording(unittest.TestCase):
    def test_record_accumulates_and_keeps_three_months(self):
        cfg = {}
        budget.record(cfg, OCT, 12.5, 'seamless_8s')
        budget.record(cfg, OCT, 20.0, 'seamless_15s')
        budget.record(cfg, OCT, 11.5, 'seamless_8s')
        m = cfg['usage']['2026-10']
        self.assertEqual((m['render_min'], m['renders']), (44.0, 3))
        self.assertEqual(m['by_format']['seamless_8s'], {'n': 2, 'min': 24.0})
        for mon in (11, 12):
            budget.record(cfg, datetime(2026, mon, 5, 12, tzinfo=timezone.utc), 1.0)
        budget.record(cfg, datetime(2027, 1, 5, 12, tzinfo=timezone.utc), 1.0)
        self.assertEqual(sorted(cfg['usage']), ['2026-11', '2026-12', '2027-01'])

    def test_failed_run_without_a_format_still_counts(self):
        cfg = {}
        budget.record(cfg, OCT, 3.0)
        self.assertEqual(cfg['usage']['2026-10']['render_min'], 3.0)
        self.assertNotIn('by_format', cfg['usage']['2026-10'])


class MonthSimulation(unittest.TestCase):
    """Bir ayın tüm render'ları. Tahminlerim yanlış çıksa bile sistem kotayı aşmadan, durmadan çalışmalı."""

    def run_month(self, factor, seed=1):
        rng = random.Random(seed)
        cfg = {}
        now = datetime(2026, 10, 1, 2, 30, tzinfo=timezone.utc)
        slots, levels = 0, {'full': 0, 'lite': 0}
        while budget.month_key(now) == '2026-10':
            d = budget.decide(cfg, now, evolve.FORMAT_NAMES)
            fmt = rng.choice(d['formats'])
            base = budget.DEFAULT_COST[fmt] if d['level'] == 'full' else 5.0
            budget.record(cfg, now, base * factor * rng.uniform(0.9, 1.1),
                          fmt if d['level'] == 'full' else None)
            levels[d['level']] += 1
            slots += 1
            now += timedelta(hours=12)
        return budget.usage_for(cfg, now - timedelta(hours=12))['render_min'], slots, levels

    def test_runs_every_slot_and_stays_within_the_budget_for_any_cost_error(self):
        for factor in (0.6, 0.8, 1.0, 1.3, 1.6, 2.2):
            minutes, slots, levels = self.run_month(factor)
            self.assertEqual(slots, 62, factor)                                # hiçbir tur atlanmadı
            self.assertLessEqual(minutes, budget.RENDER_BUDGET + 40, f'çarpan {factor}: {minutes:.0f} dk')

    def test_estimates_being_right_means_full_quality_all_month(self):
        for factor in (0.6, 0.8, 1.0):
            minutes, _, levels = self.run_month(factor)
            self.assertEqual(levels['lite'], 0, factor)
            self.assertLess(minutes, budget.RENDER_BUDGET)

    def test_total_monthly_use_leaves_room_for_the_rest(self):
        """Render + yayın (~120) + öğrenme (~240) + diğerleri (~60) ücretsiz 2000 dakikayı aşmaz."""
        minutes, _, _ = self.run_month(1.3)
        self.assertLess(minutes + 120 + 240 + 60, budget.MONTH_MINUTES)


def run_row(start, seconds, status='completed', conclusion='success'):
    """GitHub'ın çalışma listesindeki bir satır (tek işli workflow)."""
    begin = datetime.fromisoformat(start.replace('Z', '+00:00'))
    return {'created_at': start, 'run_started_at': start, 'status': status, 'conclusion': conclusion,
            'updated_at': (begin + timedelta(seconds=seconds)).strftime('%Y-%m-%dT%H:%M:%SZ')}


OCT7 = datetime(2026, 10, 7, 4, 30, tzinfo=timezone.utc)       # ayın 7'si: 25 gün kaldı (bugün dahil)
MEASURED = {'seamless_8s': {'n': 2, 'min': 20.0}, 'seamless_15s': {'n': 2, 'min': 38.0},
            'seamless_30s': {'n': 5, 'min': 122.0}}             # canlıda ölçülen: 10 / 19 / 24 dk


class WholeMonth(unittest.TestCase):
    """Bekçi yalnız render'ı değil bütün workflow'ların harcamasını sayar."""

    def test_every_run_is_rounded_up_and_costs_at_least_a_minute(self):
        now = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
        runs = [run_row('2026-10-07T03:05:00Z', 10),                                  # öğrenme: 10 sn → 1 dk
                run_row('2026-10-07T05:45:00Z', 61),                                  # yayın: 61 sn → 2 dk
                run_row('2026-10-07T04:30:00Z', 28 * 60 + 30),                        # render: 28,5 dk → 29 dk
                run_row('2026-10-07T06:00:00Z', 5, conclusion='skipped'),             # atlanan çalışma sayılmaz
                run_row('2026-10-07T11:50:00Z', 0, status='in_progress', conclusion=None),   # süren: şu ana kadar 10 dk
                {'status': 'completed', 'conclusion': 'success'}]                     # tarihi olmayan satır yok sayılır
        self.assertEqual(budget.billed_minutes(runs, now), 1 + 2 + 29 + 10)
        self.assertEqual(budget.billed_minutes([], now), 0)

    def test_the_month_is_read_in_ten_day_slices_up_to_today(self):
        day = lambda d, m=10: datetime(2026, m, d, 12, tzinfo=timezone.utc)           # noqa: E731
        self.assertEqual(budget.month_ranges(day(1)), ['2026-10-01..2026-10-01'])
        self.assertEqual(budget.month_ranges(day(7)), ['2026-10-01..2026-10-07'])
        self.assertEqual(budget.month_ranges(day(15)), ['2026-10-01..2026-10-10', '2026-10-11..2026-10-15'])
        self.assertEqual(budget.month_ranges(day(31)), ['2026-10-01..2026-10-10', '2026-10-11..2026-10-20',
                                                        '2026-10-21..2026-10-31'])
        self.assertEqual(budget.month_ranges(day(28, 2))[-1], '2026-02-21..2026-02-28')
        asked = []
        total = budget.month_total(day(15), lambda r: asked.append(r) or [run_row('2026-10-05T03:05:00Z', 90)])
        self.assertEqual((asked, total), (budget.month_ranges(day(15)), 4))

    def test_what_everything_else_spends_comes_out_of_the_renders_share(self):
        cfg = used(214, MEASURED)                                                    # render hep aynı: 214 dk
        fresh = budget.decide(cfg, OCT7, evolve.FORMAT_NAMES, used_total=214)
        self.assertEqual(fresh['formats'], evolve.FORMAT_NAMES)                      # başka harcama yokken 30 sn serbest
        # Canlıdaki durum: 5 günde testler ~300, diğerleri ~130 dakika harcadı (toplam 640)
        live = budget.decide(cfg, OCT7, evolve.FORMAT_NAMES, used_total=640)
        self.assertEqual(live['formats'], ['seamless_8s', 'seamless_15s'])
        self.assertEqual(live['level'], 'full')
        self.assertEqual(live['remaining'], 2000 - 640 - 25 * budget.OTHER_PER_DAY)  # diğer işlerin ay sonuna kadarki payı düşüldü
        self.assertAlmostEqual(live['allowance'], live['remaining'] / 50)
        tight = budget.decide(cfg, OCT7, evolve.FORMAT_NAMES, used_total=1100)
        self.assertEqual(tight['formats'], ['seamless_8s'])
        out = budget.decide(cfg, OCT7, evolve.FORMAT_NAMES, used_total=1700)
        self.assertEqual((out['level'], out['formats'], out['remaining']), ('lite', ['seamless_8s'], 0.0))

    def test_the_old_render_only_share_would_have_let_the_month_run_out(self):
        cfg = used(214, MEASURED)
        old = budget.decide(cfg, OCT7, evolve.FORMAT_NAMES)                          # toplam bilinmiyor: eski hesap
        self.assertIn('seamless_30s', old['formats'])
        self.assertIsNone(old['used_total'])
        new = budget.decide(cfg, OCT7, evolve.FORMAT_NAMES, used_total=640)
        self.assertNotIn('seamless_30s', new['formats'])
        self.assertLess(new['allowance'], old['allowance'])

    def test_the_last_recorded_total_is_used_when_github_cannot_be_read(self):
        cfg = used(214, MEASURED)
        budget.record(cfg, OCT7, 0.0, used_total=1100)
        self.assertEqual(cfg['usage']['2026-10']['all_min'], 1100.0)
        d = budget.decide(cfg, OCT7, evolve.FORMAT_NAMES)
        self.assertEqual((d['used_total'], d['formats']), (1100.0, ['seamless_8s']))
        fresher = budget.decide(cfg, OCT7, evolve.FORMAT_NAMES, used_total=300)      # okunabildiyse GitHub'ınki geçerli
        self.assertEqual(fresher['used_total'], 300)
        november = budget.decide(cfg, datetime(2026, 11, 2, 4, 30, tzinfo=timezone.utc), evolve.FORMAT_NAMES)
        self.assertIsNone(november['used_total'])                                    # yeni ayda eski toplam kullanılmaz

    def test_a_list_that_comes_back_short_never_counts_less_than_the_recorded_renders(self):
        d = budget.decide(used(1700, MEASURED), OCT7, evolve.FORMAT_NAMES, used_total=0)   # GitHub boş liste döndürdü
        self.assertEqual((d['used_total'], d['level']), (1700.0, 'lite'))

    def test_a_month_of_two_videos_a_day_fits_with_everything_else_and_some_development(self):
        """Her gün: iki render (bekçinin izin verdiği formatlardan), diğer işler 18 dk, ilk beş gün 60'ar dk test."""
        rng = random.Random(7)
        cost = {'seamless_8s': 10.0, 'seamless_15s': 19.0, 'seamless_30s': 28.0}
        cfg, total, lite = {}, 0.0, 0
        now = datetime(2026, 10, 1, 2, 30, tzinfo=timezone.utc)
        day = 0
        while budget.month_key(now) == '2026-10':
            if now.hour == 2:
                day += 1
                total += 18 + (60 if day <= 5 else 0)
            d = budget.decide(cfg, now, evolve.FORMAT_NAMES, used_total=total)
            fmt = rng.choice(d['formats'])
            minutes = cost[fmt] if d['level'] == 'full' else 5.0
            lite += d['level'] == 'lite'
            total += minutes
            budget.record(cfg, now, minutes, fmt if d['level'] == 'full' else None, used_total=total)
            now += timedelta(hours=12)
        self.assertLessEqual(total, budget.MONTH_MINUTES, f'{total:.0f} dk')
        self.assertGreater(total, 1700, 'bütçe kullanılmadan bırakıldı: videolar gereksiz kısaldı')
        self.assertEqual(lite, 0, 'ekonomik moda düşmeden bitmeli')

    def test_the_weekly_summary_shows_the_whole_spend_when_it_is_known(self):
        cfg = used(214, MEASURED)
        budget.record(cfg, OCT7, 0.0, used_total=640)
        text = report.build_report([], [], OCT7.timestamp() * 1000, usage=budget.summary(cfg, OCT7))['message']
        self.assertIn('Bu ay Actions dakikası: toplam 640 / 2000 (render 214). Render’a kalan pay 860, '
                      'kalite seviyesi: tam (1080p, her süre)', text)
        self.assertNotIn('%85', text)
        budget.record(cfg, OCT7, 0.0, used_total=1750)
        text = report.build_report([], [], OCT7.timestamp() * 1000, usage=budget.summary(cfg, OCT7))['message']
        self.assertIn('ücretsiz hakkın %85’ini aştı', text)


class CommandLine(unittest.TestCase):
    def month_runs(self, minutes):
        """Bugün açılmış, toplamı `minutes` dakika eden çalışmalar (sahte GitHub listesi)."""
        today = datetime.now(timezone.utc).strftime('%Y-%m-%dT00:00:00Z')
        return [run_row(today, 60 * 10) for _ in range(minutes // 10)]

    def test_plan_counts_every_workflow_not_only_the_renders(self):
        with World() as w, tempfile.TemporaryDirectory() as tmp:
            now = datetime.now(timezone.utc)
            w.store().transaction(lambda d: d.config.update(used(100, month=budget.month_key(now))), 'tohum')
            w.all_runs = self.month_runs(1990)                               # render 100 dk ama ay neredeyse bitmiş
            code, out = cli_run('plan', '--out', tmp)
            self.assertEqual(code, 0, out)
            self.assertIn('Bütçe: seviye lite, bu ay toplam 1990/2000 dk (render 100)', out)
            with open(os.path.join(tmp, 'release.json'), encoding='utf-8') as f:
                self.assertEqual(json.load(f)['metadata']['budget_level'], 'lite')
            asked = [c.query for c in w.srv.named('GET', r'/gh/repos/o/r/actions/runs')]
            self.assertEqual([q['created'] for q in asked if q.get('page') == '1'], budget.month_ranges(now))
            self.assertTrue(all(q['per_page'] == '100' for q in asked))
            self.assertEqual(len(asked), len(budget.month_ranges(now)) + 1, '100 çalışmadan sonra ikinci sayfa istenir')

    def test_plan_survives_github_not_answering(self):
        with World() as w, tempfile.TemporaryDirectory() as tmp:
            w.runs_status = 500
            with unittest.mock.patch('ops.net.time.sleep'):
                code, out = cli_run('plan', '--out', tmp)
            self.assertEqual(code, 0, out)
            self.assertIn('Uyarı: ay içi toplam Actions dakikası okunamadı', out)
            self.assertIn('Bütçe: seviye full, bu ay render 0/1500 dk', out)

    def test_usage_also_records_the_whole_months_total(self):
        with World() as w:
            w.all_runs = self.month_runs(640)
            code, out = cli_run('usage', '--since', str(time.time() - 600))
            self.assertEqual(code, 0, out)
            month = w.store().read().config['usage'][budget.month_key(datetime.now(timezone.utc))]
            self.assertEqual(month['all_min'], 640.0)
            self.assertIn("bütün workflow'lar 640/2000 dk", out)

    def test_usage_command_records_minutes_and_format(self):
        with World() as w, tempfile.TemporaryDirectory() as tmp:
            spec = os.path.join(tmp, 'release.json')
            with open(spec, 'w') as f:
                json.dump({'metadata': {'format': 'seamless_15s'}}, f)
            code, out = cli_run('usage', '--since', str(time.time() - 600), '--spec', spec)
            self.assertEqual(code, 0, out)
            cfg = w.store().read().config
            month = cfg['usage'][budget.month_key(datetime.now(timezone.utc))]
            self.assertAlmostEqual(month['render_min'], 11.0, delta=0.3)       # 10 dk + 1 dk kurulum payı
            self.assertEqual(month['by_format']['seamless_15s']['n'], 1)

    def test_usage_command_tolerates_a_missing_spec_and_rejects_a_bad_timestamp(self):
        with World() as w:
            code, _ = cli_run('usage', '--since', str(time.time() - 60), '--spec', '/yok/release.json')
            self.assertEqual(code, 0)
            self.assertEqual(w.store().read().config['usage'][budget.month_key(datetime.now(timezone.utc))]
                             ['renders'], 1)
            code, out = cli_run('usage', '--since', 'dün')
            self.assertEqual(code, 1)
            self.assertIn('::error::', out)

    def test_plan_follows_the_budget(self):
        with World() as w, tempfile.TemporaryDirectory() as tmp:
            now = datetime.now(timezone.utc)
            w.store().transaction(lambda d: d.config.update(used(1700, month=budget.month_key(now))), 'tohum')
            code, out = cli_run('plan', '--out', tmp)
            self.assertEqual(code, 0, out)
            with open(os.path.join(tmp, 'release.json'), encoding='utf-8') as f:
                spec = json.load(f)
            self.assertEqual((spec['width'], spec['height'], spec['duration']), (720, 1280, 8))
            self.assertEqual(spec['metadata']['budget_level'], 'lite')
            self.assertIn('Bütçe: seviye lite', out)

    def test_plan_in_a_fresh_month_is_full_quality_with_sound(self):
        with World(), tempfile.TemporaryDirectory() as tmp:
            code, out = cli_run('plan', '--out', tmp)
            self.assertEqual(code, 0, out)
            with open(os.path.join(tmp, 'release.json'), encoding='utf-8') as f:
                spec = json.load(f)
            self.assertEqual((spec['width'], spec['height']), (1080, 1920))
            self.assertTrue(spec['sound'])
            self.assertEqual(spec['metadata']['budget_level'], 'full')
            self.assertIn('ses:', out)

    def test_manual_spec_bypasses_the_budget(self):
        with World(), tempfile.TemporaryDirectory() as tmp:
            os.environ['SPEC_IN'] = json.dumps({'video_id': 'FF_ELLE_2', 'structure': 'spiral form'})
            try:
                code, out = cli_run('plan', '--out', tmp, '--spec-env', 'SPEC_IN')
            finally:
                del os.environ['SPEC_IN']
            self.assertEqual(code, 0, out)
            self.assertNotIn('Bütçe', out)


class ReportLines(unittest.TestCase):
    def build(self, usage):
        return report.build_report([], [], OCT.timestamp() * 1000, usage=usage)['message']

    def test_usage_line_is_shown_only_when_given(self):
        self.assertNotIn('render dakikası', self.build(None))
        text = self.build(budget.summary(used(400), OCT))
        self.assertIn('Bu ay render dakikası: 400 / 1500 (kalan 1100), kalite seviyesi: tam (1080p, her süre)', text)
        self.assertNotIn('%85', text)

    def test_warns_above_85_percent(self):
        text = self.build(budget.summary(used(1400), datetime(2026, 10, 26, 2, 30, tzinfo=timezone.utc)))
        self.assertIn('%85', text)
        self.assertIn('ekonomik moda', text)


if __name__ == '__main__':
    unittest.main()
