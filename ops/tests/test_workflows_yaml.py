"""GitHub Actions workflow dosyalarının yapısı: zamanlar, izinler, gizli değerler, hata bildirimi."""
import json
import os
import re
import subprocess
import sys
import unittest

import yaml

from ops import cli

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WF = os.path.join(ROOT, '.github', 'workflows')
OPS = {'render': 'FORMFORM render', 'publish': 'FORMFORM publish', 'learn': 'FORMFORM learn',
       'token': 'FORMFORM token', 'report': 'FORMFORM report'}
# Turlarını saatin başlattığı, kaçanını nöbetçinin (watch.yml) başlattığı workflow'lar: GitHub'da kendi zamanlayıcıları
# yoktur (boş yedek turlar dakika harcıyordu). Jeton ve rapor seyrek çalışır, kendi iki cron'unu korur.
CLOCKED = ('render', 'publish', 'learn', 'tiktok')
SELF_SCHEDULED = ('token', 'report')
SECRETS = {'render': set(), 'publish': {'IG_ACCESS_TOKEN', 'CLOUDINARY_API_KEY', 'CLOUDINARY_API_SECRET'},
           'learn': {'IG_ACCESS_TOKEN', 'TIKTOK_CLIENT_KEY', 'TIKTOK_CLIENT_SECRET'},   # TikTok: kayıtlı jetonu açmak için
           'token': {'IG_ACCESS_TOKEN'}, 'report': set()}


def load(name):
    with open(os.path.join(WF, f'{name}.yml'), encoding='utf-8') as f:
        text = f.read()
    return yaml.safe_load(text), text


def triggers(doc):
    return doc.get('on') or doc.get(True)       # YAML 1.1: 'on' anahtarı True olarak okunur


def steps(doc):
    return next(iter(doc['jobs'].values()))['steps']


def runs(doc):
    return [s['run'] for s in steps(doc) if 'run' in s]


class OpsWorkflows(unittest.TestCase):
    def test_each_has_a_manual_trigger_and_only_the_rare_ones_keep_their_own_schedule(self):
        for key, name in OPS.items():
            doc, _ = load(key)
            self.assertEqual(doc['name'], name)
            t = triggers(doc)
            self.assertIn('workflow_dispatch', t, key)
            if key in SELF_SCHEDULED:
                self.assertEqual(len(t['schedule']), 2, key)                 # iki mevsim cron'u
            else:
                self.assertNotIn('schedule', t, f'{key}: boş yedek turlar dakika harcar, nöbetçi (watch.yml) bakar')

    def test_schedule_times(self):
        from ops.schedule import JOBS
        timeout = load('render')[0]['jobs']['render']['timeout-minutes']
        for key in SELF_SCHEDULED:
            crons = [item['cron'] for item in triggers(load(key)[0])['schedule']]
            self.assertEqual(crons, [c for c, _off in JOBS[key]['crons']], key)
        for key in JOBS:                                                     # takvim: hiçbir iş tam saatte başlamaz
            for cron, _off in JOBS[key]['crons']:
                self.assertNotRegex(cron.split()[0], r'^0$', f'{key} tam saatte başlıyor')
        # Her mevsimde en kötü render süresi bitmeden yayın başlamaz
        for offset in (1, 2):
            def utc_minutes(job, off=offset):
                cron = next(c for c, o in JOBS[job]['crons'] if o == off)
                minute, hours = cron.split()[:2]
                return int(hours.split(',')[0]) * 60 + int(minute)
            gap = utc_minutes('publish') - utc_minutes('render')
            self.assertGreater(gap, timeout + 15, f'ofset UTC+{offset}: render ile yayın arasında yeterli boşluk yok')

    def test_permissions_are_minimal_but_sufficient(self):
        base = {'contents': 'write', 'issues': 'write'}
        want = {
            'render': dict(base, actions='write'),     # ay içi toplam dakikayı okur, nöbetçi turunda yayını başlatır
            'publish': base, 'learn': base,
            'token': dict(base, actions='read'),       # saat turu bu yuvayı aldı mı
            'report': dict(base, actions='read'),
        }
        for key in OPS:
            doc, _ = load(key)
            self.assertEqual(doc['permissions'], want[key], key)
        test_doc, _ = load('test')
        self.assertEqual(test_doc['permissions'], {'contents': 'read'})

    def test_workflows_do_not_self_cancel_or_use_floating_runner_labels(self):
        folder = os.path.join(ROOT, '.github', 'workflows')
        for name in os.listdir(folder):
            if not name.endswith('.yml'):
                continue
            with open(os.path.join(folder, name), encoding='utf-8') as f:
                text = f.read()
            self.assertNotIn('gh run cancel', text, name)
            self.assertNotIn('ubuntu-latest', text, name)
            self.assertNotIn('actions/checkout@v4', text, name)
            self.assertNotIn('actions/setup-python@v5', text, name)
            self.assertIn('ubuntu-24.04', text, name)
            self.assertIn('actions/checkout@v7', text, name)

    def test_never_triggers_on_untrusted_events(self):
        for key in list(OPS) + ['test']:
            doc, text = load(key)
            self.assertNotIn('pull_request_target', text)
            if key in OPS:
                self.assertLessEqual(set(triggers(doc)), {'schedule', 'workflow_dispatch'}, key)

    def test_secrets_used_are_exactly_the_documented_ones(self):
        for key in OPS:
            _, text = load(key)
            used = set(re.findall(r'secrets\.([A-Z_]+)', text)) - {'GITHUB_TOKEN'}
            self.assertEqual(used, SECRETS[key], key)

    def test_secrets_are_not_printed(self):
        for key in OPS:
            _, text = load(key)
            for line in text.splitlines():
                if re.match(r'\s*(run|-\s*run):', line) or line.strip().startswith(('echo', 'printenv', 'env ')):
                    self.assertNotRegex(line, r'secrets\.|IG_ACCESS_TOKEN|CLOUDINARY_API_SECRET|TIKTOK_CLIENT_SECRET')
            self.assertNotRegex(text, r'set -x|ACTIONS_STEP_DEBUG')

    def test_inputs_never_interpolated_into_shell(self):
        """Elle girilen değerler yalnız ortam değişkeni olarak geçer (komut enjeksiyonu olmasın)."""
        for key in OPS:
            doc, _ = load(key)
            for script in runs(doc):
                self.assertNotRegex(script, r'\$\{\{\s*(inputs|github\.event|github\.head_ref)', key)

    def test_failure_alert_and_resolve_steps_present_and_named_like_the_workflow(self):
        for key, name in OPS.items():
            doc, _ = load(key)
            alert = next(s for s in steps(doc) if s.get('if') == 'failure()')
            self.assertIn(f'-m ops alert --workflow "{name}"', alert['run'])
            self.assertIn('--run-url "$RUN_URL"', alert['run'])
            resolve = next(s for s in steps(doc) if '-m ops resolve' in s.get('run', ''))
            self.assertTrue(str(resolve['if']).startswith('success()'), key)
            if key in SELF_SCHEDULED:        # yanlış mevsim turu açık bir hata kaydını yanlışlıkla kapatmasın
                self.assertIn("steps.season.outputs.run == 'true'", resolve['if'], key)
            else:
                self.assertEqual(resolve['if'], 'success()', key)
            self.assertIn(f'-m ops resolve --workflow "{name}"', resolve['run'])
            self.assertTrue(resolve.get('continue-on-error'), 'düzeldi adımı çalışmayı bozmamalı')
            # alert adımı ana işten sonra gelir
            self.assertGreater(steps(doc).index(alert), 0)

    def test_every_ops_command_in_workflows_exists(self):
        parser_cmds = set(cli.build_parser()._subparsers._group_actions[0].choices)
        used = set()
        for key in list(OPS) + ['tiktok', 'watch']:
            for script in runs(load(key)[0]):
                used |= set(re.findall(r'-m ops ([\w-]+)', script))
        self.assertTrue(used)
        self.assertLessEqual(used, parser_cmds)
        self.assertEqual(parser_cmds - used, set(), 'hiçbir workflow tarafından çağrılmayan komut var')

    def test_concurrency_prevents_overlap_without_cancelling(self):
        groups = set()
        for key in OPS:
            doc, _ = load(key)
            c = doc['concurrency']
            self.assertFalse(c['cancel-in-progress'], key)
            groups.add(c['group'])
        self.assertEqual(len(groups), len(OPS), 'iki workflow aynı grubu paylaşıyor')

    def test_clocked_workflows_have_no_season_or_slot_steps_left_and_mark_who_started_them(self):
        for key in CLOCKED:
            doc, text = load(key)
            self.assertNotIn('ops season', text, key)
            self.assertNotIn('ops slot', text, key)
            self.assertNotIn('steps.season', text, key)
            self.assertNotIn('steps.slot', text, key)
            self.assertNotIn('github.event.schedule', text, key)
            inputs = triggers(doc)['workflow_dispatch']['inputs']
            for flag in ('clock', 'watch'):                                  # saat ve nöbetçi yalnız bu işareti gönderir
                self.assertEqual((inputs[flag]['type'], inputs[flag]['default'], inputs[flag]['required']),
                                 ('boolean', False, False), f'{key}.{flag}')
            self.assertIn("inputs.clock && 'saat'", doc['run-name'], key)    # panel ve nöbetçi başlıktan tanır
            self.assertIn("inputs.watch && 'nöbet'", doc['run-name'], key)

    def test_a_render_started_by_the_watchman_starts_the_missed_publish_itself(self):
        doc, _ = load('render')
        by_name = {s.get('name'): s for s in steps(doc)}
        names = list(by_name)
        chain = by_name['Nöbetçi turu (kaçan yayını da başlat)']
        self.assertEqual(chain['if'], 'success() && inputs.watch')           # saatin başlattığı turda çalışmaz
        self.assertIn('-m ops watch --after-render', chain['run'])
        self.assertTrue(chain.get('continue-on-error'), 'yayını başlatamamak hazır render\'ı başarısız saymasın')
        self.assertLess(names.index('Release olarak yayınla'), names.index('Nöbetçi turu (kaçan yayını da başlat)'))

    def test_learn_also_reads_tiktok_after_instagram_without_ever_refreshing_its_token(self):
        doc, _ = load('learn')
        by_name = {s.get('name'): s for s in steps(doc)}
        names = list(by_name)
        tiktok = by_name['TikTok paylaşımlarını bul ve ölç']
        self.assertEqual(tiktok['run'], 'python3 -m ops tiktok-sync')          # --refresh yok: yenileme TikTok workflow'unun işi
        self.assertLess(names.index('Öğren'), names.index('TikTok paylaşımlarını bul ve ölç'))
        self.assertIn('!cancelled()', tiktok['if'])                            # Instagram adımı hata verse de çalışır
        self.assertFalse(tiktok.get('continue-on-error'), 'TikTok okuma hatası görünür olmalı (hata bildirimi)')
        self.assertLess(names.index('TikTok paylaşımlarını bul ve ölç'), names.index('Hata bildirimi'))
        # öğrenme turu TikTok'un eşzamanlılık grubunda değil: bu yüzden yenileme yapamaz
        self.assertNotEqual(doc['concurrency']['group'], load('tiktok')[0]['concurrency']['group'])

    def test_python_dependencies_only_where_needed(self):
        for key in ('publish', 'learn', 'token', 'report'):
            _, text = load(key)
            self.assertNotIn('pip install', text, f'{key}: yalnız standart kütüphane yeter, kurulum süre/dakika yer')
            self.assertNotIn('setup-python', text)
        _, text = load('render')
        self.assertIn('pip install -r render/requirements.txt', text)


class TiktokWorkflow(unittest.TestCase):
    """Saatin, nöbetçinin, elle ya da Worker'ın ilettiği kodla çalışan TikTok akışı: aynı güvenceler."""

    def setUp(self):
        self.doc, self.text = load('tiktok')

    def test_runs_on_the_clock_and_by_hand_with_the_documented_actions(self):
        self.assertEqual(self.doc['name'], 'FORMFORM tiktok')
        t = triggers(self.doc)
        self.assertEqual(set(t), {'workflow_dispatch'})                        # güvensiz olay ve boş yedek tur yok
        inputs = t['workflow_dispatch']['inputs']
        self.assertEqual(inputs['action']['options'], ['connect', 'upload', 'stats', 'sync'])
        self.assertEqual(inputs['action']['default'], 'upload')                # saat ve nöbetçi yalnız işaret gönderir: varsayılan upload
        self.assertEqual(set(inputs), {'action', 'code', 'replace', 'dry_run', 'clock', 'watch'})
        self.assertNotIn('pull_request', self.text)

    def test_the_schedule_matches_the_shared_calendar_and_the_worker_clock(self):
        from ops.schedule import JOBS, WATCHED
        self.assertIn('tiktok', WATCHED)                                       # kendi cron'u yok: kaçan yuvaya nöbetçi bakar
        # render bitmeden TikTok başlamaz: her mevsimde render + zaman aşımı + pay
        timeout = load('render')[0]['jobs']['render']['timeout-minutes']
        for offset in (1, 2):
            def utc_minutes(job, off=offset):
                cron = next(c for c, o in JOBS[job]['crons'] if o == off)
                minute, hours = cron.split()[:2]
                return int(hours.split(',')[0]) * 60 + int(minute)
            self.assertGreater(utc_minutes('tiktok') - utc_minutes('render'), timeout + 15, f'UTC+{offset}')
        # Worker saati de aynı işi aynı saatlerde başlatır
        js = ("import { JOBS } from './dashboard/clock.js'; console.log(JSON.stringify(JOBS.find((j) => j.key === 'tiktok')));")
        p = subprocess.run(['node', '--input-type=module', '-e', js], cwd=ROOT, capture_output=True, text=True)
        if p.returncode == 0:
            job = json.loads(p.stdout)
            self.assertEqual((job['file'], job['times']), ('tiktok.yml', list(JOBS['tiktok']['local'])))

    def test_the_run_name_marks_who_started_the_run(self):
        name = self.doc['run-name']
        for word in ("inputs.clock && 'saat'", "inputs.watch && 'nöbet'", 'inputs.action'):
            self.assertIn(word, name)

    def test_permissions_and_concurrency_match_the_other_flows_and_do_not_overlap_them(self):
        self.assertEqual(self.doc['permissions'], {'contents': 'write', 'issues': 'write'})
        c = self.doc['concurrency']
        self.assertFalse(c['cancel-in-progress'])        # jeton yenileme ile yükleme aynı anda çalışıp jetonu bozmasın
        self.assertEqual(c['group'], 'formform-tiktok')
        self.assertNotIn(c['group'], {load(k)[0]['concurrency']['group'] for k in OPS})

    def test_secrets_are_exactly_the_documented_ones_and_never_printed(self):
        used = set(re.findall(r'secrets\.([A-Z_]+)', self.text)) - {'GITHUB_TOKEN'}
        self.assertEqual(used, {'TIKTOK_CLIENT_KEY', 'TIKTOK_CLIENT_SECRET'})
        for line in self.text.splitlines():
            if re.match(r'\s*(run|-\s*run):', line) or line.strip().startswith(('echo', 'printenv', 'env ')):
                self.assertNotRegex(line, r'secrets\.|TIKTOK_CLIENT_SECRET')
        self.assertNotRegex(self.text, r'set -x|ACTIONS_STEP_DEBUG')

    def test_inputs_reach_the_shell_only_through_the_environment(self):
        for script in runs(self.doc):
            self.assertNotRegex(script, r'\$\{\{\s*(inputs|github\.event|github\.head_ref)')
        env = next(iter(self.doc['jobs'].values()))['env']
        self.assertEqual(env['TIKTOK_CODE'], '${{ inputs.code }}')
        self.assertIn('python3 -m ops tiktok-connect', self.text)
        self.assertNotIn('--code ', self.text)                               # kod komut satırına yazılmaz

    def test_the_authorization_code_is_masked_before_it_can_be_logged(self):
        names = [s.get('name', '') for s in steps(self.doc)]
        mask = steps(self.doc)[names.index('Kodu günlükte gizle')]
        self.assertIn('::add-mask::$TIKTOK_CODE', mask['run'])
        self.assertLess(names.index('Kodu günlükte gizle'), names.index('Bağlan'))

    def test_each_action_runs_only_its_own_step_and_clock_runs_only_upload(self):
        by_name = {s.get('name'): s for s in steps(self.doc)}
        self.assertEqual(by_name['Bağlan']['if'], "inputs.action == 'connect'")
        self.assertEqual(by_name['İstatistikleri oku']['if'], "inputs.action == 'stats'")
        sync = by_name['Paylaşılanları bul ve ölç']
        self.assertEqual(sync['if'], "inputs.action == 'sync'")
        # bu workflow tek eşzamanlılık grubunda çalışır: jetonu burada yenilemek güvenlidir
        self.assertIn('python3 -m ops tiktok-sync --refresh', sync['run'])
        self.assertIn('--dry-run', sync['run'])
        # saat ve nöbetçi yalnız işaret gönderir, action varsayılanı upload'tır: connect, stats ve sync çalışmaz
        self.assertEqual(by_name['Taslak yükle']['if'], "inputs.action == 'upload'")
        self.assertIn('--platform tiktok', by_name['Taslak yükle']['run'])
        self.assertIn('--dry-run', by_name['Taslak yükle']['run'])

    def test_failure_alert_and_resolve_steps_are_named_like_the_workflow(self):
        alert = next(s for s in steps(self.doc) if s.get('if') == 'failure()')
        self.assertIn('-m ops alert --workflow "FORMFORM tiktok"', alert['run'])
        self.assertIn('--run-url "$RUN_URL"', alert['run'])
        resolve = next(s for s in steps(self.doc) if '-m ops resolve' in s.get('run', ''))
        self.assertEqual(resolve['if'], 'success()')
        self.assertIn('-m ops resolve --workflow "FORMFORM tiktok"', resolve['run'])
        self.assertTrue(resolve.get('continue-on-error'))

    def test_needs_no_python_packages(self):
        self.assertNotIn('pip install', self.text)
        self.assertNotIn('setup-python', self.text)


class RenderWorkflow(unittest.TestCase):
    def setUp(self):
        self.doc, self.text = load('render')
        self.names = [s.get('name', '') for s in steps(self.doc)]

    def test_step_order(self):
        order = ['Tarif (evrim motoru)', 'Render', 'Çıktıyı doğrula', 'Release olarak yayınla']
        idx = [self.names.index(n) for n in order]
        self.assertEqual(idx, sorted(idx))

    def test_manual_spec_is_optional_and_goes_through_env(self):
        spec = triggers(self.doc)['workflow_dispatch']['inputs']['spec']
        self.assertFalse(spec['required'])
        self.assertEqual(self.doc['jobs']['render']['env']['FORMFORM_SPEC'], '${{ inputs.spec }}')
        self.assertIn('--spec-env FORMFORM_SPEC', self.text)

    def test_release_contract_matches_what_publish_reads(self):
        self.assertIn('--title "render $RENDER_ID"', self.text)             # PICK READY: adı "render " ile başlar
        self.assertIn('--notes-file out/release.json', self.text)           # gövde = tarif + caption + metadata
        self.assertIn('out/video.mp4', self.text)                           # asset adı video.mp4
        self.assertIn('--latest=false', self.text)
        self.assertIn('.[30:]', self.text)                                  # son 30 render kalır

    def test_render_reads_the_planned_file(self):
        self.assertIn('--spec out/release.json', self.text)

    def test_check_requires_audio_when_the_recipe_has_sound(self):
        self.assertIn('python render/check.py out/video.mp4 --spec out/release.json', self.text)
        self.assertIn("grep -q ' aac '", self.text)                         # runner'da AAC kodlayıcı var mı

    def test_minutes_are_recorded_even_when_the_render_fails(self):
        first = steps(self.doc)[0]
        self.assertIn('JOB_T0=$(date +%s)', first['run'])                   # her şeyden önce sayaç başlar
        usage = next(s for s in steps(self.doc) if 'ops usage' in s.get('run', ''))
        self.assertEqual(usage['if'], 'always()')
        self.assertTrue(usage.get('continue-on-error'), 'kayıt hatası render sonucunu bozmamalı')
        self.assertIn('--since "$JOB_T0"', usage['run'])
        self.assertIn('--spec out/release.json', usage['run'])
        self.assertEqual(steps(self.doc)[-1], usage)                        # en son adım: hata/çözüldü adımlarından sonra

    def test_timeout_covers_the_longest_video_with_room(self):
        self.assertEqual(self.doc['jobs']['render']['timeout-minutes'], 45)


class CliEntry(unittest.TestCase):
    def test_module_runs_from_repo_root_with_system_python(self):
        p = subprocess.run([sys.executable, '-m', 'ops', '--help'], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('publish', p.stdout)

    def test_ops_imports_only_standard_library_besides_render_package(self):
        """Yayın/öğrenme/jeton/rapor workflow'larında pip kurulumu yok: dış bağımlılık kullanılmamalı."""
        stdlib = set(sys.stdlib_module_names)
        allowed_local = {'ops', 'formform'}
        for fn in os.listdir(os.path.join(ROOT, 'ops')):
            if not fn.endswith('.py'):
                continue
            with open(os.path.join(ROOT, 'ops', fn), encoding='utf-8') as f:
                src = f.read()
            for mod in re.findall(r'^\s*(?:from|import)\s+([A-Za-z_][\w]*)', src, re.M):
                self.assertTrue(mod in stdlib or mod in allowed_local, f'ops/{fn}: dış bağımlılık {mod}')

    def test_formform_library_import_does_not_pull_numpy(self):
        """ops yalnız render/formform/library'yi okur. numpy yoksa (yayın işi) çalışabilmeli."""
        code = ("import sys\nsys.modules['numpy']=None\nsys.modules['scipy']=None\n"
                "import ops.evolve, ops.cli, ops.publish, ops.learn, ops.report\nprint('ok')")
        p = subprocess.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(p.stdout.strip(), 'ok', p.stderr)


class WatchWorkflow(unittest.TestCase):
    """Nöbetçi: workflow başına yedek cron'ların yerini alan tek tur."""

    def setUp(self):
        self.doc, self.text = load('watch')

    def test_runs_twice_a_day_after_the_slots_in_both_seasons_and_by_hand(self):
        from ops.schedule import JOBS, WATCH_GRACE_MIN, cron_strings
        self.assertEqual(self.doc['name'], 'FORMFORM watch')
        self.assertTrue(self.text.startswith('name: FORMFORM watch\n\n#'))
        t = triggers(self.doc)
        self.assertEqual(set(t), {'schedule', 'workflow_dispatch'})
        crons = [item['cron'] for item in t['schedule']]
        self.assertEqual(crons, cron_strings('watch'))
        self.assertEqual(len(crons), 1, 'mevsim başına ayrı cron boş tur demektir: nöbetçi saate değil duruma bakar')
        minute, hours = crons[0].split()[:2]
        self.assertEqual(len(hours.split(',')), 2, 'günde iki tur: her biri 1 dakika sayılır')
        # Her mevsimde, en geç yuvadan (TikTok) en az bekleme payı kadar sonra: o tur "kaçtı" sayılabilsin
        for offset in (1, 2):
            tiktok = next(c for c, o in JOBS['tiktok']['crons'] if o == offset).split()
            for watch_hour, slot_hour in zip(hours.split(','), tiktok[1].split(',')):
                gap = (int(watch_hour) * 60 + int(minute)) - (int(slot_hour) * 60 + int(tiktok[0]))
                self.assertGreaterEqual(gap, WATCH_GRACE_MIN, f'UTC+{offset}')
                self.assertLess(gap, 180, f'UTC+{offset}: kaçan tur saatlerce beklemesin')

    def test_can_start_workflows_but_cannot_write_code_or_data(self):
        self.assertEqual(self.doc['permissions'], {'contents': 'read', 'issues': 'write', 'actions': 'write'})
        self.assertEqual(set(re.findall(r'secrets\.([A-Z_]+)', self.text)) - {'GITHUB_TOKEN'}, set())
        c = self.doc['concurrency']
        self.assertFalse(c['cancel-in-progress'])
        self.assertNotIn(c['group'], {load(k)[0]['concurrency']['group'] for k in list(OPS) + ['tiktok']})

    def test_it_is_cheap_one_command_no_python_packages(self):
        self.assertLessEqual(self.doc['jobs']['watch']['timeout-minutes'], 5)
        self.assertNotIn('pip install', self.text)
        self.assertNotIn('setup-python', self.text)
        self.assertEqual([s['run'] for s in steps(self.doc) if 'ops watch' in s.get('run', '')], ['python3 -m ops watch'])

    def test_failure_alert_and_resolve_steps_are_named_like_the_workflow(self):
        alert = next(s for s in steps(self.doc) if s.get('if') == 'failure()')
        self.assertIn('-m ops alert --workflow "FORMFORM watch"', alert['run'])
        self.assertIn('--run-url "$RUN_URL"', alert['run'])
        resolve = next(s for s in steps(self.doc) if '-m ops resolve' in s.get('run', ''))
        self.assertEqual(resolve['if'], 'success()')                         # temiz nöbet açık kaydı kapatır
        self.assertIn('-m ops resolve --workflow "FORMFORM watch"', resolve['run'])
        self.assertTrue(resolve.get('continue-on-error'))

    def test_every_watched_job_can_be_started_with_the_watch_flag(self):
        from ops import watch
        from ops.schedule import WATCHED
        self.assertEqual(set(watch.FILES), set(WATCHED))
        for job in WATCHED:
            doc, _ = load(watch.FILES[job].removesuffix('.yml'))
            self.assertIn('watch', triggers(doc)['workflow_dispatch']['inputs'], job)
            self.assertNotIn('schedule', triggers(doc), f'{job}: hem kendi cron\'u hem nöbetçi çift tur açar')


class TestsWorkflow(unittest.TestCase):
    """Testler dakika harcar: aynı kod iki kez sınanmasın, eskiyen tur iptal edilsin."""

    def setUp(self):
        self.doc, self.text = load('test')

    def test_runs_on_pull_requests_and_by_hand_but_not_on_every_push(self):
        t = triggers(self.doc)
        self.assertEqual(set(t), {'pull_request', 'workflow_dispatch'})
        self.assertIn('ops/**', t['pull_request']['paths'])
        self.assertIn('.github/workflows/**', t['pull_request']['paths'])

    def test_a_newer_push_to_the_same_pull_request_cancels_the_running_one(self):
        c = self.doc['concurrency']
        self.assertTrue(c['cancel-in-progress'])
        self.assertIn('github.event.pull_request.number', c['group'])
        self.assertNotIn(c['group'], {load(k)[0]['concurrency']['group'] for k in list(OPS) + ['tiktok', 'watch']})


class PanelWorkflow(unittest.TestCase):
    """Canlı panel main’e gelen dashboard değişikliğiyle aynı Worker adresine yüklenir."""

    def test_deploys_standalone_assets_only_from_main(self):
        doc, text = load('panel')
        self.assertEqual(doc['name'], 'FORMFORM panel')
        t = triggers(doc)
        self.assertEqual(set(t), {'push', 'workflow_dispatch'})
        push = t['push']
        self.assertEqual(push['branches'], ['main'])
        self.assertIn('dashboard/**', push['paths'])
        self.assertIn('.github/workflows/panel.yml', push['paths'])
        self.assertEqual(doc['permissions'], {'contents': 'read'})
        self.assertNotIn('pull_request', text)
        self.assertNotIn('pull_request_target', text)
        self.assertNotIn('schedule', text)
        job = doc['jobs']['deploy']
        self.assertTrue(doc['concurrency']['cancel-in-progress'])
        self.assertEqual(doc['concurrency']['group'], 'formform-panel')
        deploy = next(s for s in job['steps'] if s.get('uses', '').startswith('cloudflare/wrangler-action@'))
        self.assertEqual(deploy['with']['workingDirectory'], 'dashboard')
        self.assertEqual(deploy['with']['command'], 'deploy')
        self.assertEqual(deploy['with']['apiToken'], '${{ secrets.CLOUDFLARE_API_TOKEN }}')
        for line in text.splitlines():
            if re.match(r'\s*(run|-\s*run):', line) or line.strip().startswith(('echo', 'printenv')):
                self.assertNotRegex(line, r'secrets\.')
        with open(os.path.join(ROOT, 'dashboard', 'wrangler.json'), encoding='utf-8') as f:
            cfg = json.load(f)
        self.assertEqual(cfg['name'], 'formform-dashboard')
        self.assertEqual(cfg['assets']['directory'], './standalone')
        self.assertEqual(cfg['assets']['binding'], 'ASSETS')
        self.assertEqual(cfg['main'], 'clock.js')
        self.assertEqual(cfg['triggers']['crons'], ['*/5 * * * *'])
        self.assertTrue(cfg['workers_dev'])
        self.assertFalse(cfg['preview_urls'])

    def test_the_dispatch_key_uses_a_github_secret_name_github_allows_and_skips_cleanly_when_absent(self):
        """GitHub adı GITHUB_ ile başlayan secret oluşturmaya izin vermez: eski ad yüzünden saat Worker'ı hiç çalışmadı."""
        doc, text = load('panel')
        step = next(s for s in doc['jobs']['deploy']['steps'] if s.get('name') == 'Saat anahtarını Worker’a yaz')
        self.assertNotIn('secrets.GITHUB_', text)
        self.assertEqual(step['env']['DISPATCH_TOKEN'], '${{ secrets.DISPATCH_TOKEN }}')
        self.assertNotIn('if', step)                          # step.if içinde adımın kendi env'i görünmez: atlama komutun içinde
        run = step['run']
        self.assertIn('[ -z "$DISPATCH_TOKEN" ]', run)
        self.assertIn('exit 0', run)
        self.assertIn('''printf '%s' "$DISPATCH_TOKEN" | npx --yes wrangler@4.86.0 secret put GITHUB_DISPATCH_TOKEN''', run)
        self.assertNotRegex(run, r'echo\s+"?\$DISPATCH')

    def test_tiktok_worker_keys_are_written_from_secrets_without_printing_them(self):
        doc, text = load('panel')
        step = next(s for s in doc['jobs']['deploy']['steps'] if s.get('name') == 'TikTok anahtarlarını Worker’a yaz')
        self.assertEqual(step['env']['TIKTOK_CLIENT_KEY'], '${{ secrets.TIKTOK_CLIENT_KEY }}')
        self.assertEqual(step['env']['TIKTOK_CONNECT_KEY'], '${{ secrets.TIKTOK_CONNECT_KEY }}')
        run = step['run']
        for name in ('TIKTOK_CLIENT_KEY', 'TIKTOK_CONNECT_KEY'):
            self.assertIn(f'''printf '%s' "${name}" | npx --yes wrangler@4.86.0 secret put {name}''', run)   # stdin, komut satırı değil
        self.assertNotRegex(run, r'echo\s+"?\$TIKTOK')                                                     # değerler yazdırılmaz
        self.assertIn('exit 0', run)                      # anahtar yoksa panel yayını başarısız olmaz, bağlanma yolu kapalı kalır
        names = [s.get('name') for s in doc['jobs']['deploy']['steps']]
        self.assertGreater(names.index('TikTok anahtarlarını Worker’a yaz'), names.index('Paneli yükle'))


if __name__ == '__main__':
    unittest.main()
