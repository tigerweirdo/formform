"""Yönetim paneli (dashboard/): mantığı Python koduyla aynı sonucu vermeli, sayfa güvenlik kurallarına uymalı.

Panelin hesapları dashboard/src/logic.js içindedir ve Node ile çalıştırılır (Node yoksa atlanır).
Her sayı Python'daki karşılığıyla karşılaştırılır: panel bir sayıyı farklı gösterirse sistem sessizce yanlış yönetilir.
"""
import datetime
import json
import os
import random
import re
import shutil
import subprocess
import unittest
from collections import Counter

from ops import evolve, report, scoring, store
from ops.jsnum import to_fixed
from ops.tests.refs import rand_dna, rand_video
from ops.tests.test_workflows_yaml import OPS, load, triggers

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(ROOT, 'dashboard', 'src')
LOGIC = os.path.join(SRC, 'logic.js')
HAS_NODE = shutil.which('node') is not None
NOW_MS = 1_790_000_000_123

_RUNNER = """
const FF = require(process.argv[1]);
let input = '';
process.stdin.on('data', d => input += d);
process.stdin.on('end', () => {
  const job = JSON.parse(input);
  const out = job.calls.map(c => { const v = c.fn.split('.').reduce((o, k) => o[k], FF); return typeof v === 'function' ? v.apply(null, c.args) : v; });
  process.stdout.write(JSON.stringify(out, (k, v) => typeof v === 'number' && !isFinite(v) ? String(v) : v));
});
"""


def js(*calls):
    """js(('fn', arg1, arg2), ...) -> sonuç listesi. NaN ve Infinity metin olarak döner."""
    job = {'calls': [{'fn': c[0], 'args': list(c[1:])} for c in calls]}
    p = subprocess.run(['node', '-e', _RUNNER, LOGIC], input=json.dumps(job), capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip() or 'node hatası')
    return json.loads(p.stdout)


TRANSPORT = os.path.join(SRC, 'transport.js')
_TX_PRELUDE = r"""
const T = require(process.argv[1]); const FF = require(process.argv[2]);
const calls = [];
function resp(status, body, headers) {
  headers = headers || {};
  const text = typeof body === 'string' ? body : JSON.stringify(body);
  return { ok: status >= 200 && status < 300, status, headers: { get: k => (headers[k.toLowerCase()] !== undefined ? headers[k.toLowerCase()] : null) },
           text: async () => text, json: async () => JSON.parse(text) };
}
const TOKEN = 'github_pat_TESTTOKEN123';
function make(handler, extra) {
  return T.create(Object.assign({ fetch: async (url, init) => { calls.push({ url, init }); return handler(url, init); },
    getToken: () => TOKEN, owner: 'o', repo: 'r', minIntervalMs: 10 }, extra || {}));
}
const strip = u => u.replace(/([?&])_=\d+&?/, '$1').replace(/[?&]$/, '');
const S = 'github';
async function code(p) { try { await p; return 'ok'; } catch (e) { return e.code + '|' + e.message; } }
const sleep = ms => new Promise(r => setTimeout(r, ms));
"""


def tx(script):
    """transport.js'i Node'da, sahte bir fetch ile çalıştırır. Betikte T, FF, make, resp, calls, S hazırdır; sonuç JSON döner."""
    code_ = _TX_PRELUDE + '(async () => {' + script + '})().then(r => process.stdout.write(JSON.stringify(r)), e => { console.error(e && e.stack || e); process.exit(1); });'
    p = subprocess.run(['node', '-e', code_, TRANSPORT, LOGIC], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip() or 'node hatası')
    return json.loads(p.stdout)


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as f:
        return f.read()


def lib():
    return {'structure': evolve.STRUCTURES, 'palette': evolve.PALETTES, 'motion': evolve.MOTION_QUALITIES,
            'lighting': evolve.LIGHTING_STYLES, 'format': evolve.FORMAT_NAMES, 'material': evolve.MATERIALS,
            'transformation': evolve.TRANSFORMATIONS, 'hook': evolve.CAPTION_OPENINGS}


def iso(ms):
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')


def ms(text):
    return int(datetime.datetime.fromisoformat(text.replace('Z', '+00:00')).timestamp() * 1000)


@unittest.skipUnless(HAS_NODE, 'Node yok')
class ScoringParity(unittest.TestCase):
    def test_constants_are_the_python_constants(self):
        C = js(('C',))[0]
        self.assertEqual((C['MIN_WEIGHT'], C['MAX_WEIGHT']), (scoring.MIN_WEIGHT, scoring.MAX_WEIGHT))
        self.assertEqual((C['PRIOR_STRENGTH'], C['TREND_WINDOW'], C['TREND_MIN']),
                         (scoring.PRIOR_STRENGTH, scoring.TREND_WINDOW, scoring.TREND_MIN))
        self.assertEqual((C['EXPLORATION_RATE'], C['ELITE_RATE'], C['ELITE_POOL'], C['MIN_SCORED_FOR_ELITES']),
                         (evolve.EXPLORATION_RATE, evolve.ELITE_RATE, evolve.ELITE_POOL, evolve.MIN_SCORED_FOR_ELITES))
        self.assertEqual((C['GENE_MUTATION'], C['LIST_MUTATION']), (evolve.GENE_MUTATION, evolve.LIST_MUTATION))

    def test_csv_reader_matches_the_store_reader(self):
        rng = random.Random(3)
        rows = [rand_video(rng, i) for i in range(30)]
        rows[1]['caption'] = 'Tırnak "içinde", virgül\nve satır sonu\n\n#etiket'
        rows[2]['materials'] = 'a, b, "c"'
        text = store._csv_write(rows, store.VIDEO_COLUMNS)
        self.assertEqual(js(('parseCSV', text))[0], store._csv_read(text))
        self.assertEqual(js(('parseCSV', ''))[0], [])
        self.assertEqual(js(('parseCSV', ','.join(store.DNA_COLUMNS) + '\n'))[0], [])

    def test_performance_score_matches_python(self):
        rng = random.Random(11)
        rows = []
        for i in range(400):
            r = rand_video(rng, i)
            if rng.random() < 0.15:
                r['views_72h'] = ''
            if rng.random() < 0.1:
                r['avg_watch_time'] = rng.choice(['0', 'abc', '-3'])
            rows.append(r)
        got = js(*[('performanceScore', r) for r in rows])
        for r, g in zip(rows, got):
            self.assertEqual(g, scoring.performance_score(r), r)

    def test_score_parts_add_up_to_the_score(self):
        row = rand_video(random.Random(5), 1)
        row.update(views_72h='900', likes='80', comments='5', shares='9', saves='14', avg_watch_time='6.5',
                   loop_strategy='seamless_15s')
        p = js(('scoreParts', row))[0]
        w = p['weights']
        self.assertAlmostEqual(w[0] * p['reach'] + w[1] * p['adjusted'] + w[2] * p['retention'], p['score'], delta=0.0051)
        self.assertEqual(w, [0.45, 0.30, 0.25])
        row['avg_watch_time'] = ''
        self.assertEqual(js(('scoreParts', row))[0]['weights'], [0.65, 0.35, 0])

    def test_dna_weights_match_python(self):
        rng = random.Random(21)
        for size in (1, 5, 17, 60):
            rows = [rand_video(rng, i) for i in range(size)]
            want = scoring.dna_weights(rows, 'T')
            got = js(('dnaWeights', rows, 'T'))[0]
            self.assertEqual(len(got), len(want), size)
            for g, w in zip(got, want):
                self.assertEqual({k: g[k] for k in w}, w)

    def test_dna_weights_empty_without_scores(self):
        self.assertEqual(js(('dnaWeights', [], ''))[0], [])
        rows = [rand_video(random.Random(1), i, scored=False) for i in range(5)]
        self.assertEqual(js(('dnaWeights', rows, ''))[0], [])

    def test_find_due_matches_python(self):
        rng = random.Random(8)
        rows = [rand_video(rng, i, scored=rng.random() < 0.5) for i in range(80)]
        for r in rows:
            if rng.random() < 0.3:
                r['views_24h'] = ''
        want = [(d['video_id'], d['snapshot_window']) for d in scoring.find_due(rows, NOW_MS)]
        got = [(d['video_id'], d['snapshot_window']) for d in js(('findDue', rows, NOW_MS))[0]]
        self.assertEqual(got, want)
        self.assertTrue(want)

    def test_trend_relative_matches_the_weights_baseline(self):
        rng = random.Random(4)
        rows = [rand_video(rng, i) for i in range(25)]
        ordered = js(('orderedScored', rows))[0]
        t = js(('trendRelative', ordered))[0]
        self.assertEqual(len(t['rel']), len(ordered))
        self.assertAlmostEqual(sum(t['rel']) / len(t['rel']), t['globalRelative'], places=9)


@unittest.skipUnless(HAS_NODE, 'Node yok')
class ReportParity(unittest.TestCase):
    """Panelin özeti, haftalık özet e-postasındaki sayıların aynısını göstermeli."""

    def setUp(self):
        rng = random.Random(33)
        self.videos = [rand_video(rng, i) for i in range(70)]
        self.dna = scoring.dna_weights(self.videos, 'T')
        self.text = report.build_report(self.videos, self.dna, NOW_MS)['message']
        self.stats = js(('buildStats', self.videos, self.dna, NOW_MS))[0]

    def test_counts(self):
        posted7 = int(re.search(r'Son 7 günde yayınlanan: (\d+) video', self.text).group(1))
        total, scored = map(int, re.search(r'Toplam yayınlanan: (\d+), skorlanan: (\d+)', self.text).groups())
        self.assertEqual(self.stats['posted7'], posted7)
        self.assertEqual((len(self.stats['published']), len(self.stats['scored'])), (total, scored))
        self.assertGreater(scored, 10)

    def test_recent_average_and_change(self):
        n, avg = re.search(r'Son (\d+) skorlu videonun ortalaması: (\S+)', self.text).groups()
        self.assertEqual(self.stats['lastN'], int(n))
        self.assertEqual(to_fixed(self.stats['lastMean'], 1), avg)
        m = re.search(r'Önceki (\d+) videoya göre: ([+-]?\d+)%', self.text)
        self.assertIsNotNone(m)
        sign = '+' if self.stats['change'] >= 0 else ''
        self.assertEqual(sign + to_fixed(self.stats['change'], 0), m.group(2))

    def test_format_comparison(self):
        section = self.text.split('4. SÜRE / FORMAT')[1].split('\n\n')[0]
        want = {m.group(1): (int(m.group(2)), m.group(3)) for m in
                re.finditer(r'^- (\S+): (\d+) video, ortalama skor (\S+?)(?:,|$)', section, re.M)}
        got = {f['key']: (f['n'], to_fixed(f['mean'], 1)) for f in self.stats['byFormat']}
        self.assertEqual(got, want)

    def test_morning_and_evening(self):
        am = re.search(r'Sabah \(08:00\): (\d+) video, ortalama skor (\S+)', self.text)
        pm = re.search(r'Akşam \(20:00\): (\d+) video, ortalama skor (\S+)', self.text)
        h = self.stats['hours']
        self.assertEqual((h['morning']['n'], to_fixed(h['morning']['mean'], 1)), (int(am.group(1)), am.group(2)))
        self.assertEqual((h['evening']['n'], to_fixed(h['evening']['mean'], 1)), (int(pm.group(1)), pm.group(2)))

    def test_learned_weights_listed_like_the_report(self):
        learned = {l['dimension']: l['rows'] for l in self.stats['learned']}
        for dim, name in (('structure', 'Yapı'), ('palette', 'Palet'), ('format', 'Süre')):
            line = next((ln for ln in self.text.splitlines() if ln.startswith(f'- {name}: en iyi')), None)
            if line is None:
                self.assertNotIn(dim, learned)
                continue
            best = learned[dim][0]
            self.assertIn(f"{best['value']} ({to_fixed(best['weight'], 2)}, n={int(best['n'])})", line)

    def test_health_numbers(self):
        pending = len([r for r in self.stats['pending72']])
        m = re.search(r'(\d+) videonun 72 saatlik ölçümü hâlâ boş', self.text)
        self.assertEqual(pending, int(m.group(1)) if m else 0)
        m = re.search(r'(\d+) satırda instagram_id yok', self.text)
        self.assertEqual(self.stats['noId'], int(m.group(1)) if m else 0)
        m = re.search(r'İzlenme süresi verisi olan skorlu video: (\d+)/(\d+)', self.text)
        self.assertEqual((self.stats['withWatch'], len(self.stats['scored'])), tuple(map(int, m.groups())))


@unittest.skipUnless(HAS_NODE, 'Node yok')
class SelectionOdds(unittest.TestCase):
    """Kapalı form olasılıklar, evrim motorunun gerçek çıktısının sıklığıyla örtüşmeli."""

    N = 8000

    def videos(self, count):
        rng = random.Random(77)
        out = []
        for i in range(count):
            r = rand_video(rng, i)
            r['video_id'] = f'FF_{i:03d}'
            r['performance_score'] = str(round(rng.uniform(10, 90), 2))
            r['published_at'] = iso(NOW_MS - (i + 1) * 12 * 3600_000)
            r['loop_strategy'] = rng.choice(evolve.FORMAT_NAMES)
            out.append(r)
        return out

    def frequencies(self, videos, dna):
        rng = random.Random(2026).random
        now = datetime.datetime(2026, 10, 3, tzinfo=datetime.timezone.utc)
        fields = {'structure': 'structure', 'palette': 'palette', 'motion': 'motion_quality', 'lighting': 'lighting',
                  'format': 'format', 'hook': 'hook', 'sound': 'sound'}
        counts = {d: Counter() for d in fields}
        for _ in range(self.N):
            meta = evolve.generate(videos, dna, rng, now)['metadata']
            for d, f in fields.items():
                counts[d][meta[f]] += 1
        return counts

    def check(self, count):
        videos = self.videos(count)
        dna = scoring.dna_weights(videos, '')
        counts = self.frequencies(videos, dna)
        odds = js(('selectionOdds', lib(), dna, videos))[0]
        for dim, rows in odds['dims'].items():
            self.assertAlmostEqual(sum(r['p'] for r in rows), 1.0, places=9, msg=dim)
            for r in rows:
                freq = counts[dim][r['value']] / self.N
                self.assertAlmostEqual(r['p'], freq, delta=0.03, msg=f"{dim}: {r['value']}")
        return odds

    def test_with_elite_parents(self):
        odds = self.check(14)
        self.assertTrue(odds['pool']['enabled'])
        self.assertAlmostEqual(odds['modes']['elite'], 0.35, places=9)
        self.assertAlmostEqual(odds['modes']['explore'] + odds['modes']['elite'] + odds['modes']['weights'], 1.0, places=9)

    def test_elite_stays_off_below_four_scored_videos(self):
        odds = self.check(3)
        self.assertFalse(odds['pool']['enabled'])
        self.assertEqual(odds['modes']['elite'], 0)

    def test_without_any_learning_everything_is_uniform(self):
        odds = js(('selectionOdds', lib(), [], []))[0]
        for dim, rows in odds['dims'].items():
            for r in rows:
                self.assertAlmostEqual(r['p'], 1 / len(rows), places=9, msg=dim)


@unittest.skipUnless(HAS_NODE, 'Node yok')
class Schedule(unittest.TestCase):
    def test_workflow_table_matches_the_workflow_files(self):
        wf = {w['key']: w for w in self.workflows()}
        self.assertEqual(set(wf), set(OPS))
        for key, name in OPS.items():
            doc, _ = load(key)
            self.assertEqual(wf[key]['name'], name)
            self.assertEqual(wf[key]['file'], f'{key}.yml')
            from ops.schedule import JOBS
            self.assertEqual(wf[key]['cron'], JOBS[key]['crons'][0][0], key)          # takvim: ops/schedule.py
            if 'schedule' in triggers(doc):                                       # kendi zamanlayıcısı olanlar (jeton, rapor)
                self.assertEqual(wf[key]['cron'], triggers(doc)['schedule'][0]['cron'], key)
            self.assertEqual([s['cron'] for s in wf[key]['seasons']], [c for c, _o in JOBS[key]['crons']], key)
            self.assertEqual([s['offset'] for s in wf[key]['seasons']], [o for _c, o in JOBS[key]['crons']], key)
            self.assertEqual(wf[key]['timeout'], doc['jobs'][key]['timeout-minutes'], key)
            self.assertEqual(sorted(wf[key]['inputs']), sorted((triggers(doc)['workflow_dispatch'] or {}).get('inputs', {})), key)

    def workflows(self):
        # WORKFLOWS bir dizidir: js() işlevleri çağırır, bu yüzden küçük bir sarmalayıcı kullanılır
        p = subprocess.run(['node', '-e', "console.log(JSON.stringify(require(process.argv[1]).WORKFLOWS))", LOGIC],
                           capture_output=True, text=True)
        return json.loads(p.stdout)

    def test_cron_slots_match_an_independent_python_calendar(self):
        start, end = ms('2026-09-01T00:00:00Z'), ms('2026-11-15T00:00:00Z')
        days = [datetime.datetime(2026, 9, 1, tzinfo=datetime.timezone.utc) + datetime.timedelta(days=i) for i in range(76)]

        def at(d, h, m):
            return int(d.replace(hour=h, minute=m).timestamp() * 1000)
        want = {
            '30 3,15 * * *': [at(d, h, 30) for d in days for h in (3, 15)],
            '45 4,16 * * *': [at(d, h, 45) for d in days for h in (4, 16)],
            '5 */3 * * *': [at(d, h, 5) for d in days for h in range(0, 24, 3)],
            '7 2 1,15 * *': [at(d, 2, 7) for d in days if d.day in (1, 15)],
            '17 6 * * 1': [at(d, 6, 17) for d in days if d.weekday() == 0],
        }
        for cron, expected in want.items():
            got = js(('slotsBetween', cron, start, end))[0]
            self.assertEqual(got, [t for t in expected if start <= t < end], cron)

    def test_next_runs_are_after_now_and_sorted(self):
        got = js(('nextRuns', '45 4,16 * * *', ms('2026-10-03T10:00:00Z'), 3))[0]
        self.assertEqual([iso(t) for t in got], ['2026-10-03T16:45:00.000Z', '2026-10-04T04:45:00.000Z', '2026-10-04T16:45:00.000Z'])
        self.assertEqual(js(('nextRuns', '45 4,16 * * *', ms('2026-10-03T16:45:00Z'), 1))[0], [ms('2026-10-04T04:45:00Z')])

    def test_cron_is_described_in_berlin_time(self):
        summer = js(*[('describeCron', c) for c in ('30 4,16 * * *', '45 5,17 * * *', '5 */3 * * *', '7 3 1,15 * *', '17 7 * * 1')])
        winter = js(*[('describeCron', c) for c in ('30 5,17 * * *', '45 6,18 * * *', '7 4 1,15 * *', '17 8 * * 1')])
        labels = ['Her gün 06:30, 18:30', 'Her gün 07:45, 19:45', 'Her 3 saatte bir (her turun 05. dakikası)',
                  'Ayın 1 ve 15’i 05:07', 'Her pazartesi 09:17']
        self.assertEqual(summer, labels)
        self.assertEqual(winter, [labels[0], labels[1], labels[3], labels[4]])

    def test_expected_slots_follow_berlin_daylight_saving(self):
        render = {'cron': '30 4,16 * * *', 'seasons': [
            {'cron': '30 4,16 * * *', 'offset': 2}, {'cron': '30 5,17 * * *', 'offset': 1}]}
        summer = js(('expectedSlots', render, ms('2026-10-03T00:00:00Z'), ms('2026-10-04T00:00:00Z')))[0]
        winter = js(('expectedSlots', render, ms('2026-11-03T00:00:00Z'), ms('2026-11-04T00:00:00Z')))[0]
        self.assertEqual([iso(t) for t in summer], ['2026-10-03T04:30:00.000Z', '2026-10-03T16:30:00.000Z'])
        self.assertEqual([iso(t) for t in winter], ['2026-11-03T05:30:00.000Z', '2026-11-03T17:30:00.000Z'])

    def test_adherence_finds_late_and_missed_slots(self):
        """Gerçek gözlem biçiminde: learn turları 00:05, 03:05, 06:05, 09:05. Yalnız ikisi çalışmış, ikisi de çok geç."""
        def run(t):
            return {'event': 'schedule', 'created_at': t}
        runs = [run('2026-10-03T05:10:04Z'), run('2026-10-03T11:33:53Z'), {'event': 'workflow_dispatch', 'created_at': '2026-10-03T07:00:00Z'}]
        a = js(('adherence', '5 */3 * * *', runs, ms('2026-10-02T23:30:00Z'), ms('2026-10-03T12:00:00Z')))[0]
        self.assertEqual([i['kind'] for i in a['items']], ['missed', 'verylate', 'missed', 'verylate'])
        self.assertEqual((a['total'], a['missed'], a['verylate'], a['ontime']), (4, 2, 2, 0))
        d1 = ms('2026-10-03T05:10:04Z') - ms('2026-10-03T03:05:00Z')
        d2 = ms('2026-10-03T11:33:53Z') - ms('2026-10-03T09:05:00Z')
        self.assertEqual((a['items'][1]['delay'], a['items'][3]['delay']), (d1, d2))
        self.assertEqual(a['medianDelay'], (d1 + d2) / 2)
        self.assertEqual(a['extra'], 0)       # elle başlatılan çalışma zamanlı tura sayılmaz

    def test_adherence_on_time_late_and_grace_period(self):
        since = ms('2026-10-03T00:00:00Z')
        runs = [{'event': 'schedule', 'created_at': '2026-10-03T03:30:40Z'}, {'event': 'schedule', 'created_at': '2026-10-03T15:48:00Z'}]
        a = js(('adherence', '30 3,15 * * *', runs, since, ms('2026-10-03T16:00:00Z')))[0]
        self.assertEqual([i['kind'] for i in a['items']], ['ontime', 'ontime'])
        self.assertEqual(a['extra'], 0)
        # 15:30 turunun çalışması 50 dakika geç gelirse "gecikmeli"
        late = [runs[0], {'event': 'schedule', 'created_at': '2026-10-03T16:20:00Z'}]
        a = js(('adherence', '30 3,15 * * *', late, since, ms('2026-10-03T17:00:00Z')))[0]
        self.assertEqual([i['kind'] for i in a['items']], ['ontime', 'late'])
        # henüz bekleme payı dolmamış tur "kaçırıldı" sayılmaz, payı dolunca sayılır
        a = js(('adherence', '30 3,15 * * *', runs[:1], since, ms('2026-10-03T16:00:00Z')))[0]
        self.assertEqual((a['total'], a['missed']), (1, 0))
        a = js(('adherence', '30 3,15 * * *', runs[:1], since, ms('2026-10-03T17:45:00Z')))[0]
        self.assertEqual((a['total'], a['missed']), (2, 1))

    def test_an_off_season_success_does_not_fill_the_slot(self):
        """Kış saati cron’u yazın başarılı boş tur olarak biter. O tur sabah yuvasını doldurmaz."""
        render = {'cron': '30 4,16 * * *', 'seasons': [
            {'cron': '30 4,16 * * *', 'offset': 2}, {'cron': '30 5,17 * * *', 'offset': 1}]}
        since, now = ms('2026-10-03T04:00:00Z'), ms('2026-10-03T12:00:00Z')
        slots = js(('expectedSlots', render, since, now))[0]
        winter = {'event': 'schedule', 'conclusion': 'success', 'created_at': '2026-10-03T05:40:00Z', 'display_title': 'render (kış saati)'}
        missed = js(('adherenceSlots', slots, [winter], now))[0]
        self.assertTrue(js(('offSeasonRun', winter))[0])
        self.assertEqual([i['kind'] for i in missed['items']], ['missed'])
        self.assertEqual(missed['extra'], 0)
        summer = {'event': 'schedule', 'conclusion': 'success', 'created_at': '2026-10-03T04:40:00Z', 'display_title': 'render (yaz saati)'}
        filled = js(('adherenceSlots', slots, [summer, winter], now))[0]
        self.assertEqual([i['kind'] for i in filled['items']], ['ontime'])
        self.assertEqual(filled['extra'], 0)

    def test_a_clock_dispatch_fills_the_slot_and_a_late_schedule_is_extra(self):
        """Saat turu yuvayı zamanında doldurur. Saatler sonra gelen GitHub yedeği gecikme sayılmaz."""
        since, now = ms('2026-10-04T04:00:00Z'), ms('2026-10-04T12:00:00Z')
        clock = {'event': 'workflow_dispatch', 'conclusion': 'success', 'created_at': '2026-10-04T04:31:00Z', 'display_title': 'render (saat)'}
        late = {'event': 'schedule', 'conclusion': 'success', 'created_at': '2026-10-04T10:42:00Z', 'display_title': 'render (yaz saati)'}
        a = js(('adherence', '30 4,16 * * *', [clock, late], since, now))[0]
        self.assertEqual([i['kind'] for i in a['items']], ['ontime'])
        self.assertEqual(a['verylate'], 0)
        self.assertEqual(a['extra'], 1)
        hand = {'event': 'workflow_dispatch', 'conclusion': 'success', 'created_at': '2026-10-04T04:31:00Z', 'display_title': 'render (elle)'}
        missed = js(('adherence', '30 4,16 * * *', [hand], since, ms('2026-10-04T08:00:00Z')))[0]
        self.assertEqual(missed['missed'], 1)

    def test_a_run_started_by_the_watchman_fills_the_slot_late_instead_of_showing_it_as_missed(self):
        """Saat turu kaçırdı, nöbetçi üç saat sonra başlattı: yuva boş değil, geç dolmuş görünür."""
        since, now = ms('2026-10-04T04:00:00Z'), ms('2026-10-04T12:00:00Z')
        watch = {'event': 'workflow_dispatch', 'conclusion': 'success', 'created_at': '2026-10-04T07:26:00Z', 'display_title': 'render (nöbet)'}
        a = js(('adherence', '30 4,16 * * *', [watch], since, now))[0]
        self.assertEqual((a['missed'], a['verylate'], a['extra']), (0, 1, 0))


@unittest.skipUnless(HAS_NODE, 'Node yok')
class Health(unittest.TestCase):
    NOW = ms('2026-10-03T12:00:00Z')

    def run_checks(self, videos=(), config=None, runs=None, releases=(), issues=(), workflows=None, sources=None, adherence=None):
        stats = js(('buildStats', list(videos), [], self.NOW))[0]
        state = {'config': config or {}, 'workflows': workflows or [], 'runs': runs or {}, 'releases': list(releases), 'issues': list(issues)}
        src = sources or {k: {'ok': True} for k in ('videos', 'config', 'releases', 'issues')}
        ctx = {'nowMs': self.NOW, 'stats': stats, 'state': state, 'sources': src, 'adherence': adherence or {}}
        return {c['id']: c for c in js(('healthChecks', ctx))[0]}

    def video(self, hours_ago, **kw):
        r = rand_video(random.Random(1), 1, scored=False)
        r.update(video_id=f'FF_{hours_ago}', published_at=iso(self.NOW - int(hours_ago * 3600_000)), instagram_id='1', **kw)
        return r

    def test_last_post_levels(self):
        self.assertEqual(self.run_checks([self.video(5)])['last-post']['level'], 'ok')
        self.assertEqual(self.run_checks([self.video(20)])['last-post']['level'], 'warn')
        self.assertEqual(self.run_checks([self.video(40)])['last-post']['level'], 'crit')
        self.assertEqual(self.run_checks([])['last-post']['level'], 'info')

    def test_overdue_measurements(self):
        c = self.run_checks([self.video(90)])
        self.assertEqual(c['pending']['level'], 'crit')
        c = self.run_checks([self.video(40)])
        self.assertEqual(c['pending']['level'], 'warn')
        c = self.run_checks([self.video(30)])
        self.assertEqual(c['pending']['level'], 'ok')

    def test_token_levels(self):
        def cfg(days):
            return {'ig_token_updated_at': iso(self.NOW - 5 * 86400_000), 'ig_token_expires_at': iso(self.NOW + days * 86400_000)}
        self.assertEqual(self.run_checks(config=cfg(40))['token']['level'], 'ok')
        self.assertEqual(self.run_checks(config=cfg(15))['token']['level'], 'warn')
        self.assertEqual(self.run_checks(config=cfg(3))['token']['level'], 'crit')
        self.assertEqual(self.run_checks(config={})['token']['level'], 'info')

    def test_open_alert_issues_and_stuck_release(self):
        c = self.run_checks(issues=[{'number': 3, 'title': '[FORMFORM] Hata: FORMFORM publish'}, {'number': 4, 'title': 'başka'}])
        self.assertEqual(c['issues']['level'], 'crit')
        self.assertIn('FORMFORM publish', c['issues']['detail'])
        rel = [{'id': 1, 'name': 'claimed FF_X', 'published_at': iso(self.NOW - 3 * 3600_000), 'tag_name': 't', 'html_url': 'u'}]
        self.assertEqual(self.run_checks(releases=rel)['stuck']['level'], 'crit')
        rel[0]['published_at'] = iso(self.NOW - 10 * 60_000)
        self.assertNotIn('stuck', self.run_checks(releases=rel))

    def test_missing_render_before_publish(self):
        c = self.run_checks(releases=[])        # sıradaki yayın 17:45 UTC (19:45 Berlin), saat 12:00: 5 sa 45 dk var
        self.assertNotIn('no-render', c)
        c = {x['id']: x for x in js(('healthChecks', {'nowMs': ms('2026-10-03T16:30:00Z'), 'stats': js(('buildStats', [], [], 0))[0],
              'state': {'releases': [], 'config': {}, 'workflows': [], 'runs': {}, 'issues': []},
              'sources': {'releases': {'ok': True}}, 'adherence': {}}))[0]}
        self.assertEqual(c['no-render']['level'], 'warn')

    def test_failed_and_disabled_workflows(self):
        ok = {'status': 'completed', 'conclusion': 'success', 'created_at': iso(self.NOW - 3600_000), 'html_url': 'u', 'display_title': 'x'}
        bad = dict(ok, conclusion='failure')
        sources = {'videos': {'ok': True}, 'runs:learn': {'ok': True}, 'runs:render': {'ok': True}}
        c = self.run_checks(runs={'learn': [bad, ok], 'render': [ok]}, sources=sources)
        self.assertEqual(c['wf-learn']['level'], 'crit')
        self.assertEqual(c['wf-render']['level'], 'ok')
        wfs = [{'path': '.github/workflows/render.yml', 'state': 'disabled_inactivity'}]
        c = self.run_checks(runs={'render': [ok]}, sources=sources, workflows=wfs)
        self.assertEqual(c['wf-render']['level'], 'crit')

    def test_missing_source_adds_no_false_alarm(self):
        c = self.run_checks(sources={'videos': {'ok': False}})
        self.assertEqual(c, {})


@unittest.skipUnless(HAS_NODE, 'Node yok')
class Adapters(unittest.TestCase):
    """GitHub bağlayıcısının yanıt biçimleri (uydurma değerlerle, yalnız yapı)."""

    def test_file_text_reads_resource_text_and_prefixed_text_and_blob(self):
        text = 'a,b\n1,2\n'
        r = js(('fileText', {'content': [{'type': 'text', 'text': 'successfully downloaded text file (SHA: x)'},
                                         {'type': 'resource', 'resource': {'uri': 'repo://x', 'text': text}}]}),
               ('fileText', {'content': [{'type': 'text', 'text': 'successfully downloaded text file (SHA: x)'},
                                         {'type': 'text', 'text': '[Resource from github at repo://x/y.csv] ' + text}]}),
               ('fileText', {'content': [{'type': 'resource', 'resource': {'uri': 'repo://x', 'blob': 'YSxiCjEsMgo='}}]}),
               ('fileText', {'content': [], 'payload': 'successfully downloaded text file (SHA: x)'}),
               ('fileText', {'content': [{'type': 'text', 'text': 'successfully downloaded text file'}], 'payload': 'a,b\n'}))
        self.assertEqual(r, [text, text, text, None, 'a,b\n'])

    def test_run_release_issue_and_commit_lists(self):
        runs = {'total_count': 1, 'workflow_runs': [{'id': 1, 'name': 'learn (zamanlı)', 'display_title': 'learn (zamanlı)', 'workflow_id': 9,
                'run_number': 2, 'event': 'schedule', 'status': 'completed', 'conclusion': 'success', 'head_branch': 'main',
                'html_url': 'https://github.com/o/r/actions/runs/1', 'created_at': '2026-10-03T11:33:53Z',
                'run_started_at': '2026-10-03T11:33:53Z', 'updated_at': '2026-10-03T11:34:05Z', 'actor': {'login': 'k'}}]}
        got = js(('parseRuns', runs), ('parseRuns', json.dumps(runs)), ('runDuration', runs['workflow_runs'][0]),
                 ('runStatus', runs['workflow_runs'][0]), ('runStatus', {'status': 'in_progress'}),
                 ('runStatus', {'status': 'completed', 'conclusion': 'timed_out'}), ('runStatus', {'status': 'queued'}))
        self.assertEqual(got[0][0]['id'], 1)
        self.assertEqual(got[0], got[1])
        self.assertEqual(got[2], 12000)
        self.assertEqual(got[3:], ['ok', 'running', 'fail', 'queued'])
        rel = js(('parseReleases', [{'id': 5, 'name': 'published FF_1', 'tag_name': 'render-FF_1', 'html_url': 'u', 'published_at': '2026-10-03T09:31:51Z'}]),
                 ('parseRelease', {'id': 5, 'name': 'claimed FF_9', 'published_at': '2026-10-03T09:31:51Z'}),
                 ('parseRelease', {'id': 6, 'name': 'başka bir ad'}))
        self.assertEqual(rel[0][0]['id'], 5)
        self.assertEqual((rel[1]['state'], rel[1]['videoId']), ('claimed', 'FF_9'))
        self.assertEqual(rel[2]['state'], 'other')
        iss = js(('parseIssues', {'issues': [{'number': 1, 'title': 't', 'state': 'OPEN'}], 'totalCount': 1}),
                 ('parseCommits', [{'sha': 'a', 'html_url': 'u', 'commit': {'message': 'm', 'committer': {'date': 'd'}}}]),
                 ('parseWorkflows', {'workflows': [{'id': 1, 'name': 'n', 'path': 'p', 'state': 'active'}]}),
                 ('parseConfig', '{"a": 1}'), ('parseConfig', 'bozuk'), ('parseConfig', '[]'))
        self.assertEqual(iss[0][0]['number'], 1)
        self.assertEqual(iss[1][0]['at'], 'd')
        self.assertEqual(iss[2][0]['state'], 'active')
        self.assertEqual(iss[3:], [{'a': 1}, {}, {}])

    def test_unexpected_shapes_raise_instead_of_showing_nothing(self):
        for fn, bad in (('parseRuns', {}), ('parseWorkflows', {}), ('parseReleases', {}), ('parseIssues', []), ('parseCommits', {})):
            with self.assertRaises(RuntimeError, msg=fn):
                js((fn, bad))


@unittest.skipUnless(HAS_NODE, 'Node yok')
class ManualRunInputs(unittest.TestCase):
    def test_example_spec_passes_and_is_one_line(self):
        spec = json.loads(read('render', 'ornek-spec.json'))
        got = js(('checkSpec', json.dumps(spec, indent=2), lib()))[0]
        self.assertTrue(got['ok'], got)
        self.assertNotIn('\n', got['line'])

    def test_problems_are_reported_in_plain_language(self):
        bad = js(('checkSpec', '{"video_id": "x"}', lib()), ('checkSpec', 'not json', lib()), ('checkSpec', '[]', lib()),
                 ('checkSpec', '', lib()),
                 ('checkSpec', json.dumps({'video_id': 'FF_OK1', 'structure': 'yok', 'palette': 'violet-pink',
                                           'motion_quality': evolve.MOTION_QUALITIES[0], 'lighting': evolve.LIGHTING_STYLES[0]}), lib()))
        self.assertFalse(bad[0]['ok'])
        self.assertTrue(any('video_id' in p for p in bad[0]['problems']))
        self.assertFalse(bad[1]['ok'])
        self.assertFalse(bad[2]['ok'])
        self.assertTrue(bad[3]['ok'] and bad[3]['empty'])
        self.assertTrue(any('kütüphanede yok' in p for p in bad[4]['problems']))

    def test_video_id_rule_is_the_cli_rule(self):
        cli = read('ops', 'cli.py')
        self.assertIn(r"[A-Za-z0-9_-]{3,64}", cli)
        self.assertIn(r"/^[A-Za-z0-9_-]{3,64}$/", read('dashboard', 'src', 'logic.js'))


@unittest.skipUnless(HAS_NODE, 'Node yok')
class Demo(unittest.TestCase):
    """Örnek veri gerçek değildir ama gerçek mantıktan geçer: skorlar ve ağırlıklar Python'la aynıdır."""

    def setUp(self):
        self.demo = js(('makeDemo', NOW_MS, lib(), 7))[0]

    def test_is_deterministic_and_plausible(self):
        self.assertEqual(self.demo, js(('makeDemo', NOW_MS, lib(), 7))[0])
        self.assertNotEqual(self.demo['videos'], js(('makeDemo', NOW_MS, lib(), 8))[0]['videos'])
        v = self.demo['videos']
        self.assertGreater(len(v), 30)
        self.assertTrue(all(r['published_at'] for r in v))
        self.assertTrue(any(r['performance_score'] == '' or r['performance_score'] is None for r in v), 'son videolar henüz ölçülmemiş olmalı')
        self.assertFalse(any(r['video_id'].startswith('TEST_') for r in v))
        self.assertEqual(len({r['video_id'] for r in v}), len(v))

    def test_scores_and_weights_are_what_python_computes(self):
        rows = [{k: ('' if x is None else str(x)) for k, x in r.items()} for r in self.demo['videos']]
        for r in rows:
            if r['views_72h'] != '':
                self.assertEqual(float(r['performance_score']), scoring.performance_score(r), r['video_id'])
        want = scoring.dna_weights(rows, '')
        got = self.demo['dna']
        self.assertEqual(len(got), len(want))
        for g, w in zip(got, want):
            self.assertEqual({k: g[k] for k in ('key', 'avg_score', 'sample_count', 'weight')},
                             {k: w[k] for k in ('key', 'avg_score', 'sample_count', 'weight')})

    def test_runs_have_the_shape_the_panel_reads(self):
        self.assertEqual(set(self.demo['runs']), set(OPS))
        for runs in self.demo['runs'].values():
            self.assertTrue(runs)
            times = [r['created_at'] for r in runs]
            self.assertEqual(times, sorted(times, reverse=True))
            self.assertTrue(all(r['event'] == 'schedule' and r['html_url'].startswith('https://github.com/') for r in runs))

    def test_demo_includes_a_plausible_actions_quota(self):
        b = self.demo['billing']
        self.assertEqual(b, {'ok': True, 'included': 2000, 'used': 640, 'paid': 0, 'remaining': 1360})


class ActionsQuota(unittest.TestCase):
    def test_remaining_free_minutes_ignore_paid_overage(self):
        q = js(('actionsQuota', {'included_minutes': 2000, 'total_minutes_used': 2100, 'total_paid_minutes_used': 100}))[0]
        self.assertEqual(q, {'ok': True, 'included': 2000, 'used': 2000, 'paid': 100, 'remaining': 0})

    def test_a_missing_permission_is_not_a_number(self):
        self.assertEqual(js(('actionsQuota', {'unavailable': True, 'code': 'forbidden'}))[0], {'ok': False, 'code': 'forbidden'})
        self.assertFalse(js(('actionsQuota', {}))[0]['ok'])

    def test_waiting_videos_count_toward_the_first_score_not_as_zero_progress(self):
        """Skor 72 saatte yazılır. O saate kadar kart, en eski yayının yaşını ilerleme sayar."""
        now = ms('2026-10-04T13:20:00Z')
        rows = [
            {'video_id': 'FF_1', 'instagram_id': '1', 'published_at': '2026-10-02T23:49:39.000Z', 'views_24h': '19', 'views_72h': '', 'performance_score': ''},
            {'video_id': 'FF_2', 'instagram_id': '2', 'published_at': '2026-10-03T10:04:53.000Z', 'views_24h': '11', 'views_72h': '', 'performance_score': ''},
            {'video_id': 'FF_3', 'instagram_id': '3', 'published_at': '2026-10-04T11:19:44.000Z', 'views_24h': '', 'views_72h': '', 'performance_score': ''},
        ]
        learn = js(('buildStats', rows, [], now))[0]['learn']
        self.assertEqual((learn['published'], learn['has24'], learn['has72'], learn['nextId']), (3, 2, 0, 'FF_1'))
        self.assertAlmostEqual(learn['nextAgeH'], (now - ms('2026-10-02T23:49:39.000Z')) / 3_600_000, places=5)
        self.assertEqual(learn['nextDue'], ms('2026-10-02T23:49:39.000Z') + 72 * 3_600_000)


@unittest.skipUnless(HAS_NODE, 'Node yok')
class Transport(unittest.TestCase):
    """Bağımsız sürümün GitHub REST katmanı: istekler, yanıt biçimi, izin listeleri, hata kodları, anahtar güvenliği."""

    def test_reads_become_the_expected_rest_calls(self):
        got = tx("""
          const t = make(u => u.includes('/contents/') ? resp(200, 'a,b\\n') : (u.includes('/runs') ? resp(200, { workflow_runs: [] }) : (u.includes('/actions/workflows?') ? resp(200, { workflows: [] }) : resp(200, []))));
          await t.callTool(S, 'get_file_contents', { path: 'videos.csv', ref: 'refs/heads/veri' });
          await t.callTool(S, 'actions_list', { method: 'list_workflows' });
          await t.callTool(S, 'actions_list', { method: 'list_workflow_runs', resource_id: 'render.yml', perPage: 50 });
          await t.callTool(S, 'list_releases', { perPage: 30 });
          await t.callTool(S, 'list_issues', { state: 'OPEN', perPage: 30 });
          await t.callTool(S, 'list_commits', { sha: 'veri', perPage: 5 });
          return calls.map(c => ({ url: strip(c.url), raw: c.url, method: c.init.method, headers: c.init.headers, keys: Object.keys(c.init) }));
        """)
        base = 'https://api.github.com/repos/o/r'
        self.assertEqual([c['url'] for c in got], [
            base + '/contents/videos.csv?ref=veri', base + '/actions/workflows?per_page=100', base + '/actions/workflows/render.yml/runs?per_page=50',
            base + '/releases?per_page=30', base + '/issues?state=open&per_page=30', base + '/commits?sha=veri&per_page=5'])
        for c in got:
            self.assertEqual(c['method'], 'GET')
            self.assertEqual(c['headers']['Authorization'], 'Bearer github_pat_TESTTOKEN123')
            self.assertLessEqual(set(c['headers']), {'Authorization', 'Accept'}, 'özel başlık ek CORS ön kontrolü doğurur')
            self.assertLessEqual(set(c['keys']), {'method', 'headers', 'credentials', 'referrerPolicy'}, 'cache seçeneği tarayıcıda ek başlık ekler')
            self.assertNotIn('TESTTOKEN', c['raw'])
            self.assertRegex(c['raw'], r'[?&]_=\d+')                 # önbelleği aşan zaman damgası
        self.assertEqual(got[0]['headers']['Accept'], 'application/vnd.github.raw+json')

    def test_actions_billing_reads_the_account_and_hides_a_missing_plan_permission(self):
        got = tx("""
          const t = make(u => u.includes('/settings/billing/actions') ? resp(200, { total_minutes_used: 305, total_paid_minutes_used: 0, included_minutes: 2000 }) : resp(404, {}));
          const ok = await t.callTool(S, 'actions_billing', {});
          const n = calls.length;
          const denied = make(() => resp(403, { message: 'Resource not accessible by personal access token' }));
          const no = await denied.callTool(S, 'actions_billing', {});
          const hidden = make(u => u.includes('/billing/actions')
            ? resp(401, { message: 'Requires authentication' })
            : resp(200, { usageItems: [
                { product: 'actions', sku: 'actions_linux', quantity: 19, unitType: 'minutes' },
                { product: 'actions', sku: 'actions_storage', quantity: 4, unitType: 'gb' },
                { product: 'copilot', sku: 'copilot', quantity: 8, unitType: 'minutes' }
              ] }));
          const used = await hidden.callTool(S, 'actions_billing', {});
          return { url: strip(calls[0].url), n, quota: FF.actionsQuota(ok.payload), denied: no.payload,
                   used: FF.actionsQuota(used.payload), usageUrl: calls.filter(c => c.url.includes('/billing/usage')).pop().url, auth: calls[0].init.headers.Authorization };
        """)
        self.assertEqual(got['url'], 'https://api.github.com/users/o/settings/billing/actions')
        self.assertEqual(got['n'], 1, 'özet gelince kullanım raporu çağrılmaz')
        self.assertEqual(got['quota'], {'ok': True, 'included': 2000, 'used': 305, 'paid': 0, 'remaining': 1695})
        self.assertEqual(got['denied'], {'unavailable': True, 'code': 'forbidden'})
        self.assertEqual(got['used'], {'ok': True, 'included': None, 'used': 19, 'paid': 0, 'remaining': None})
        self.assertRegex(got['usageUrl'], r'/users/o/settings/billing/usage\?year=\d{4}&month=\d{1,2}$')
        self.assertNotIn('_=', got['usageUrl'])
        self.assertEqual(got['auth'], 'Bearer github_pat_TESTTOKEN123')
        self.assertNotIn('TESTTOKEN', got['url'])

    def test_responses_have_the_shape_the_panel_reads(self):
        got = tx("""
          const run = { id: 1, event: 'schedule', status: 'completed', conclusion: 'success', created_at: '2026-10-03T11:33:53Z', updated_at: '2026-10-03T11:34:05Z', html_url: 'https://github.com/o/r/actions/runs/1', actor: { login: 'k' } };
          const issues = [{ number: 5, title: '[FORMFORM] Hata: x', state: 'open' }, { number: 6, title: 'bir PR', pull_request: {} }];
          const map = { '/contents/': 'a,b\\n1,2\\n', '/runs': { total_count: 1, workflow_runs: [run] }, '/actions/workflows?': { workflows: [{ id: 3, name: 'w', path: 'p', state: 'active' }] },
                        '/releases': [{ id: 2, name: 'published FF_1', tag_name: 't', html_url: 'u', published_at: '2026-10-03T09:31:51Z' }],
                        '/issues': issues, '/commits': [{ sha: 'a', html_url: 'u', commit: { message: 'm', committer: { date: 'd' } } }] };
          const t = make(u => { const k = Object.keys(map).find(x => u.includes(x)); return resp(200, k ? map[k] : { full_name: 'o/r' }); });
          const file = await t.callTool(S, 'get_file_contents', { path: 'videos.csv', ref: 'veri' });
          const runs = await t.callTool(S, 'actions_list', { method: 'list_workflow_runs', resource_id: 'learn.yml' });
          const wfs = await t.callTool(S, 'actions_list', { method: 'list_workflows' });
          const rel = await t.callTool(S, 'list_releases', {});
          const iss = await t.callTool(S, 'list_issues', { state: 'OPEN' });
          const com = await t.callTool(S, 'list_commits', { sha: 'veri' });
          return { text: FF.fileText(file), runs: FF.parseRuns(runs.payload), wfs: FF.parseWorkflows(wfs.payload), rel: FF.parseReleases(rel.payload),
                   iss: FF.parseIssues(iss.payload), com: FF.parseCommits(com.payload), checked: await t.check() };
        """)
        self.assertEqual(got['text'], 'a,b\n1,2\n')
        self.assertEqual(got['runs'][0]['id'], 1)
        self.assertEqual(got['wfs'][0]['state'], 'active')
        self.assertEqual(got['rel'][0]['name'], 'published FF_1')
        self.assertEqual([i['number'] for i in got['iss']], [5], 'çekme istekleri issue sayılmamalı')
        self.assertEqual(got['com'][0]['at'], 'd')
        self.assertEqual(got['checked']['name'], 'o/r')

    def test_writes_are_post_requests_with_the_expected_bodies(self):
        got = tx("""
          const t = make(() => resp(204, ''));
          const a = await t.callTool(S, 'actions_run_trigger', { method: 'run_workflow', workflow_id: 'publish.yml', ref: 'main', inputs: { dry_run: 'true' } });
          const b = await t.callTool(S, 'actions_run_trigger', { method: 'run_workflow', workflow_id: 'learn.yml', ref: 'main' });
          const c = await t.callTool(S, 'actions_run_trigger', { method: 'cancel_workflow_run', run_id: 123 });
          return { calls: calls.map(x => ({ url: x.url, method: x.init.method, body: x.init.body, headers: x.init.headers })), results: [a.payload, b.payload, c.payload] };
        """)
        base = 'https://api.github.com/repos/o/r'
        urls = [c['url'] for c in got['calls']]
        self.assertEqual(urls, [base + '/actions/workflows/publish.yml/dispatches', base + '/actions/workflows/learn.yml/dispatches', base + '/actions/runs/123/cancel'])
        self.assertTrue(all(c['method'] == 'POST' for c in got['calls']))
        self.assertEqual(json.loads(got['calls'][0]['body']), {'ref': 'main', 'inputs': {'dry_run': 'true'}})
        self.assertEqual(json.loads(got['calls'][1]['body']), {'ref': 'main'})
        self.assertIsNone(got['calls'][2].get('body'))
        self.assertEqual(got['calls'][0]['headers']['Content-Type'], 'application/json')
        self.assertNotIn('Content-Type', got['calls'][2]['headers'])
        self.assertEqual(got['results'], [None, None, None])

    def test_allow_lists_refuse_everything_else_before_any_network_call(self):
        got = tx("""
          const t = make(() => resp(200, []));
          const out = [];
          const tries = [
            ['get_file_contents', { path: 'ig_token.sealed', ref: 'veri' }], ['get_file_contents', { path: '../x', ref: 'veri' }],
            ['get_file_contents', { path: 'videos.csv/../ig_token.sealed', ref: 'veri' }], ['get_file_contents', {}],
            ['actions_list', { method: 'list_workflow_runs', resource_id: '../..' }], ['actions_list', { method: 'list_workflow_runs', resource_id: 'test.yml' }],
            ['actions_list', { method: 'list_workflow_run_artifacts' }],
            ['actions_run_trigger', { method: 'run_workflow', workflow_id: 'test.yml', ref: 'main' }],
            ['actions_run_trigger', { method: 'run_workflow', workflow_id: 'publish.yml', ref: 'feature' }],
            ['actions_run_trigger', { method: 'rerun_workflow_run', run_id: 5 }], ['actions_run_trigger', { method: 'delete_workflow_run_logs', run_id: 5 }],
            ['actions_run_trigger', { method: 'cancel_workflow_run', run_id: '5' }], ['actions_run_trigger', { method: 'cancel_workflow_run', run_id: -1 }],
            ['actions_run_trigger', { method: 'cancel_workflow_run', run_id: 1.5 }],
            ['merge_pull_request', {}], ['create_or_update_file', {}]
          ];
          for (const [tool, input] of tries) out.push(await code(t.callTool(S, tool, input)));
          out.push(await code(t.callTool('baska', 'list_releases', {})));
          return { out, calls: calls.length };
        """)
        self.assertEqual(got['calls'], 0, 'izin dışı çağrı ağa çıkmamalı')
        self.assertTrue(all(o.startswith('bad_request|') for o in got['out']), got['out'])

    def test_http_errors_map_to_distinct_codes_and_never_leak_the_token(self):
        got = tx("""
          const cases = [
            [401, { message: 'Bad credentials' }], [403, { message: 'Resource not accessible by personal access token' }],
            [403, { message: 'nope' }, { 'x-ratelimit-remaining': '0' }], [429, { message: 'slow' }], [404, { message: 'Not Found' }],
            [422, { message: 'Workflow does not have workflow_dispatch trigger' }], [500, { message: 'boom' }], [502, 'gateway'],
            [403, { message: 'sızıntı: github_pat_TESTTOKEN123' }]
          ];
          const out = [];
          for (const [status, body, headers] of cases) {
            const t = make(() => resp(status, body, headers));
            try { await t.callTool(S, 'list_releases', {}); out.push(['ok']); } catch (e) { out.push([e.code, e.message, !!e.retryable]); }
          }
          const t2 = make(() => { throw new TypeError('Failed to fetch'); });
          try { await t2.callTool(S, 'list_releases', {}); } catch (e) { out.push([e.code, e.message, !!e.retryable]); }
          return out;
        """)
        codes = [o[0] for o in got]
        self.assertEqual(codes, ['needs_reauth', 'forbidden', 'rate_limited', 'rate_limited', 'tool_error', 'tool_error', 'server_unavailable', 'server_unavailable', 'forbidden', 'network'])
        self.assertTrue(got[2][2] and got[3][2] and got[6][2] and got[9][2], 'geçici hatalar yeniden denenebilir olarak işaretlenmeli')
        self.assertFalse(got[0][2] or got[1][2] or got[4][2])
        self.assertIn('(404)', got[4][1])
        self.assertIn('workflow_dispatch', got[5][1])
        for o in got:
            self.assertNotIn('TESTTOKEN', o[1])
        self.assertIn('***', got[8][1])

    def test_without_a_token_nothing_leaves_the_page(self):
        got = tx("""
          const t = make(() => resp(200, []), { getToken: () => '' });
          return { r: await code(t.callTool(S, 'list_releases', {})), calls: calls.length };
        """)
        self.assertTrue(got['r'].startswith('token_missing|'))
        self.assertEqual(got['calls'], 0)

    def test_watching_polls_stops_on_unsubscribe_and_refreshes_on_invalidate(self):
        got = tx("""
          const events = [];
          const t = make(() => resp(200, []));
          const un = t.watchTool(S, 'list_releases', {}, ev => events.push(ev.type), { refetchInterval: 20 });
          await sleep(160); un(); const n = events.length; await sleep(90);
          const once = [];
          const t2 = make(() => resp(200, []));
          t2.watchTool(S, 'list_releases', {}, ev => once.push(ev.type), {});
          await sleep(40); const first = once.length;
          await t2.invalidate(S, 'list_issues'); await sleep(40); const other = once.length;
          await t2.invalidate(S); await sleep(40);
          const failing = []; const t3 = make(() => resp(500, { message: 'x' }));
          t3.watchTool(S, 'list_releases', {}, ev => failing.push(ev.type + ':' + (ev.error && ev.error.code)), {});
          await sleep(40);
          return { n, after: events.length, types: [...new Set(events)], first, other, last: once.length, failing };
        """)
        self.assertGreaterEqual(got['n'], 3)
        self.assertEqual(got['after'], got['n'], 'abonelik bitince yoklama durmalı')
        self.assertEqual(got['types'], ['data'])
        self.assertEqual((got['first'], got['other'], got['last']), (1, 1, 2), 'invalidate yalnız eşleşen aracı yeniler')
        self.assertEqual(got['failing'], ['error:server_unavailable'])

    def test_every_request_goes_to_api_github_com_by_default(self):
        got = tx("""
          const t = T.create({ fetch: async (u, i) => { calls.push({ u }); return resp(200, []); }, getToken: () => TOKEN, owner: 'o', repo: 'r' });
          await t.callTool(S, 'list_releases', {}); await t.check().catch(() => 0);
          return { base: T.BASE, urls: calls.map(c => c.u.split('/').slice(0, 3).join('/')) };
        """)
        self.assertEqual(got['base'], 'https://api.github.com')
        self.assertEqual(set(got['urls']), {'https://api.github.com'})

    def test_file_list_is_exactly_the_three_state_files(self):
        got = tx("return { files: T.ALLOWED_FILES, wf: T.ALLOWED_WORKFLOWS };")
        self.assertEqual(got['files'], ['videos.csv', 'dna_weights.csv', 'config.json'])
        self.assertEqual(sorted(got['wf']), sorted(f'{k}.yml' for k in OPS))
        self.assertNotIn('ig_token.sealed', got['files'])


class DashboardFiles(unittest.TestCase):
    """Sayfa kaynağı: güncel, güvenli ve belgelerle uyumlu."""

    def setUp(self):
        from dashboard import build
        self.app = build.app_source()
        self.page = read('dashboard', 'index.html')

    def test_built_page_is_up_to_date(self):
        from dashboard import build
        self.assertEqual(build.build(), self.page, 'python dashboard/build.py çalıştırın')

    def test_embedded_library_is_the_python_library(self):
        from dashboard import build
        m = re.search(r'^var LIB = (.*);$', self.page, re.M)
        self.assertEqual(json.loads(m.group(1)), build.library())
        m = re.search(r'^var FORMATS = (.*);$', self.page, re.M)
        self.assertEqual(json.loads(m.group(1)), evolve.FORMATS)
        lib_ = build.library()
        for name in lib_['material'] + lib_['transformation']:
            self.assertNotIn(', ', name)

    def test_page_fragment_has_no_document_skeleton(self):
        for tag in (r'<!doctype', r'<html[\s>]', r'<head[\s>]', r'<body[\s>]'):
            self.assertIsNone(re.search(tag, self.page.lower()), tag)
        self.assertTrue(self.page.startswith('<title>'))
        self.assertLessEqual(len(self.page.encode('utf-8')), 16 * 1024 * 1024)

    def test_no_dynamic_html_or_code_execution(self):
        """Veri (video adı, issue başlığı, caption) güvenilmez metindir: yalnız textContent ile yazılır."""
        for source in (self.app, read('dashboard', 'src', 'logic.js')):
            for banned in ('innerHTML', 'outerHTML', 'insertAdjacentHTML', 'document.write', 'eval(', 'new Function', 'setTimeout("', "setTimeout('"):
                self.assertNotIn(banned, source, banned)

    def test_only_expected_external_hosts(self):
        hosts = set(re.findall(r'https?://([a-z0-9.-]+)', self.page))
        self.assertLessEqual(hosts, {'fonts.googleapis.com', 'fonts.gstatic.com', 'github.com', 'www.w3.org'}, hosts)

    def test_only_github_tools_in_the_manifest_are_called_and_only_two_writes_exist(self):
        used = set(re.findall(r"tool:\s*'([a-z_]+)'", self.app)) | set(re.findall(r"callTool\(SERVER,\s*'([a-z_]+)'", self.app))
        manifest = json.loads(read('dashboard', 'manifest.json'))
        declared = set(manifest['mcp']['servers'][0]['tools'])
        self.assertEqual(manifest['mcp']['servers'][0]['server'], 'github')
        self.assertEqual(used, declared, 'manifest.json ile sayfanın çağırdığı araçlar aynı olmalı')
        self.assertEqual(declared, {'get_file_contents', 'actions_list', 'list_releases', 'list_issues', 'list_commits', 'actions_run_trigger'})
        methods = set(re.findall(r"method:\s*'(run_workflow|rerun_workflow_run|rerun_failed_jobs|cancel_workflow_run|delete_workflow_run_logs)'", self.app))
        self.assertEqual(methods, {'run_workflow', 'cancel_workflow_run'}, 'panel yalnız çalıştırır ve iptal eder')
        for banned in ('create_or_update_file', 'push_files', 'merge_pull_request', 'delete_file', 'issue_write'):
            self.assertNotIn(banned, self.app)

    def test_real_publish_needs_a_typed_confirmation_and_defaults_to_dry_run(self):
        self.assertIn("CONFIRM_WORD = 'PAYLAŞ'", self.app)
        self.assertRegex(self.app, r"dry_run:\s*true")
        self.assertIn('geri alınamaz', self.app)

    def test_workflow_runs_use_the_main_branch_and_string_inputs(self):
        self.assertIn("ref: 'main'", self.app)
        self.assertRegex(self.app, r"String\(")

    def test_node_can_parse_the_page_script(self):
        if not HAS_NODE:
            self.skipTest('Node yok')
        body = re.search(r'<script>\n(.*)</script>', self.page, re.S).group(1)
        p = subprocess.run(['node', '--check', '-'], input=body, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)


class StandalonePage(unittest.TestCase):
    """Ayrı bir sitede barındırılan sürüm: anahtarla çalışır, bu yüzden güvenlik kuralları daha sıkıdır."""

    def setUp(self):
        from dashboard import build
        self.build = build
        self.page = read('dashboard', 'standalone', 'index.html')
        self.headers = read('dashboard', 'standalone', '_headers')
        self.transport = read('dashboard', 'src', 'transport.js')
        self.setup = read('dashboard', 'src', 'app', 'standalone.js')

    def test_outputs_are_up_to_date(self):
        for path, text in self.build.outputs().items():
            self.assertEqual(read(os.path.relpath(path, ROOT)), text, f'{os.path.relpath(path, ROOT)}: python dashboard/build.py çalıştırın')

    def test_is_a_full_document_with_a_strict_content_security_policy(self):
        self.assertTrue(self.page.startswith('<!doctype html>'))
        meta = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)">', self.page)
        self.assertEqual(meta.group(1), self.build.CSP)
        directives = dict(d.strip().split(' ', 1) for d in self.build.CSP.split(';') if d.strip())
        self.assertEqual(directives['default-src'], "'none'")
        self.assertEqual(directives['connect-src'], 'https://api.github.com', 'anahtar başka bir adrese gidemez')
        self.assertEqual(directives['script-src'], "'unsafe-inline'", 'dış betik yüklenemez')
        self.assertEqual((directives['base-uri'], directives['form-action']), ("'none'", "'none'"))
        for line in ("Content-Security-Policy: " + self.build.CSP, "frame-ancestors 'none'", 'Referrer-Policy: no-referrer', 'X-Content-Type-Options: nosniff', 'X-Frame-Options: DENY'):
            self.assertIn(line, self.headers)
        self.assertIn('<meta name="referrer" content="no-referrer">', self.page)
        self.assertIn('noindex', self.page)

    def test_the_flag_and_the_transport_exist_only_in_this_build(self):
        self.assertIn('var STANDALONE = true;', self.page)
        self.assertIn('var RestTransport', self.page)
        artifact = read('dashboard', 'index.html')
        self.assertIn('var STANDALONE = false;', artifact)
        self.assertNotIn('RestTransport', artifact)
        self.assertNotIn('personal-access-tokens', artifact)

    def test_only_expected_external_hosts(self):
        hosts = set(re.findall(r'https?://([a-z0-9.-]+)', self.page))
        self.assertLessEqual(hosts, {'fonts.googleapis.com', 'fonts.gstatic.com', 'github.com', 'api.github.com', 'www.w3.org'}, hosts)

    def test_no_dynamic_html_or_code_execution(self):
        for name, source in (('transport.js', self.transport), ('standalone.js', self.setup)):
            for banned in ('innerHTML', 'outerHTML', 'insertAdjacentHTML', 'document.write', 'eval(', 'new Function', "setTimeout('", 'setTimeout("'):
                self.assertNotIn(banned, source, f'{name}: {banned}')

    def test_token_is_masked_stored_once_and_never_printed(self):
        self.assertRegex(self.setup, r"type: 'password'")
        self.assertEqual(len(re.findall(r"store\.set\('token', tok\)", self.setup)), 1, 'anahtar yalnız doğrulandıktan sonra saklanır')
        for name, source in (('transport.js', self.transport), ('standalone.js', self.setup)):
            self.assertNotIn('console.', source, f'{name}: anahtar günlüğe düşebilir')
            self.assertNotRegex(source, r'text:\s*[^,}]*(S\.token|tok\b)', f'{name}: anahtar ekrana yazılmamalı')
            self.assertNotRegex(source, r'textContent\s*=\s*[^;]*(S\.token|tok\b)', name)
        self.assertNotIn('localStorage', self.transport)
        self.assertIn('scrub(', self.transport)
        # yalnız ince ayarlı anahtar kabul edilir
        self.assertIn('github_pat_', self.setup)
        self.assertNotIn('ghp_', self.setup)

    def test_setup_screen_names_every_permission_the_panel_needs(self):
        for text in ('https://github.com/settings/personal-access-tokens/new', 'Only select repositories', 'Actions', 'Read and write', 'Contents', 'Issues', 'Metadata', 'Read-only'):
            self.assertIn(text, self.setup, text)
        self.assertIn('Plan', self.setup)
        # anahtarın yetkisi, taşıma katmanının kullandığı uç noktalarla örtüşür: Actions yazma yalnız iki POST için
        posts = re.findall(r"http\('POST', repoPath\('([^']+)", self.transport)
        self.assertEqual(sorted(posts), sorted(['/actions/workflows/', '/actions/runs/']))
        self.assertEqual(len(re.findall(r"http\('(PUT|PATCH|DELETE)'", self.transport)), 0, 'okuma ve iki POST dışında istek yok')

    def test_node_parses_the_page_script(self):
        if not HAS_NODE:
            self.skipTest('Node yok')
        body = re.search(r'<script>\n(.*)</script>', self.page, re.S).group(1)
        p = subprocess.run(['node', '--check', '-'], input=body, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_both_builds_share_the_same_logic_and_library(self):
        a, b = read('dashboard', 'index.html'), self.page
        for pattern in (r'^var LIB = .*;$', r'^var FORMATS = .*;$'):
            self.assertEqual(re.search(pattern, a, re.M).group(0), re.search(pattern, b, re.M).group(0))
        logic = read('dashboard', 'src', 'logic.js').rstrip('\n')
        self.assertIn(logic, a)
        self.assertIn(logic, b)


if __name__ == '__main__':
    unittest.main()
