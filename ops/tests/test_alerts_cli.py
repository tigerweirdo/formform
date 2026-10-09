"""Hata bildirimi, haftalık özet, jeton yenileme ve komut satırı."""
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO

from ops import alerts, cli, tokens
from ops.errors import TokenRefreshFailed
from ops.store import VIDEO_COLUMNS
from ops.tests.fakes import TOKEN, World, release

NOW = datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc)


def read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def read_json(path):
    return json.loads(read(path))


def cli_run(*argv):
    out = StringIO()
    with redirect_stdout(out):
        code = cli.main(list(argv))
    return code, out.getvalue()


class Alerts(unittest.TestCase):
    def test_failure_creates_assigned_issue_with_hint_and_link(self):
        with World() as w:
            alerts.remember_error('publish', 'Yayınlanacak render yok: x', 'Şunu yapın.')
            self.assertEqual(alerts.report_failure('FORMFORM publish', 'https://x/run/1', now=NOW), 'created')
            issue = w.issues[0]
            self.assertEqual(issue['title'], '[FORMFORM] Hata: FORMFORM publish')
            self.assertEqual(issue['assignees'], ['o'])
            for part in ('Yayınlanacak render yok: x', 'Şunu yapın.', 'https://x/run/1', '@o', '11:00', '(Berlin)'):
                self.assertIn(part, issue['body'])

    def test_cooldown_six_hours_then_comment(self):
        with World() as w:
            alerts.report_failure('FORMFORM learn', now=NOW)
            w.issues[0]['updated_at'] = (NOW - timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M:%SZ')
            self.assertEqual(alerts.report_failure('FORMFORM learn', now=NOW), 'cooldown')
            self.assertEqual(len(w.issues), 1)
            self.assertEqual(w.issues[0]['comments'], [])
            w.issues[0]['updated_at'] = (NOW - timedelta(hours=7)).strftime('%Y-%m-%dT%H:%M:%SZ')
            self.assertEqual(alerts.report_failure('FORMFORM learn', now=NOW), 'commented')
            self.assertEqual(len(w.issues), 1)
            self.assertEqual(len(w.issues[0]['comments']), 1)

    def test_different_workflows_do_not_share_cooldown(self):
        with World() as w:
            alerts.report_failure('FORMFORM learn', now=NOW)
            self.assertEqual(alerts.report_failure('FORMFORM publish', now=NOW), 'created')
            self.assertEqual(len(w.issues), 2)

    def test_resolve_closes_open_issue(self):
        with World() as w:
            alerts.report_failure('FORMFORM learn', now=NOW)
            alerts.report_failure('FORMFORM publish', now=NOW)
            self.assertEqual(alerts.resolve('FORMFORM learn'), 1)
            self.assertEqual([i['state'] for i in w.issues], ['closed', 'open'])
            self.assertIn('Düzeldi', w.issues[0]['comments'][0])
            self.assertEqual(alerts.resolve('FORMFORM learn'), 0)

    def test_assignment_failure_falls_back_to_unassigned_issue(self):
        with World() as w:
            def reject_assignees(c, m):
                if 'assignees' in c.json():
                    return 422, {'message': 'atanamaz'}
                return w._new_issue(c, m)
            w.srv.route('POST', r'/gh/repos/o/r/issues', reject_assignees)
            alerts.report_failure('FORMFORM render', now=NOW)
            self.assertEqual(len(w.issues), 1)
            self.assertNotIn('assignees', w.issues[0])

    def test_error_text_is_scrubbed_of_secrets(self):
        with World():
            from ops import net
            net.add_secret(TOKEN)
            alerts.remember_error('publish', f'patladı {TOKEN} sonu', 'ipucu')
            with open(alerts.error_file(), encoding='utf-8') as f:
                self.assertNotIn(TOKEN, f.read())

    def test_ping_opens_assigned_issue_and_closes_it(self):
        with World() as w:
            alerts.ping()
            self.assertEqual((w.issues[0]['title'], w.issues[0]['state'], w.issues[0]['assignees']),
                             ('[FORMFORM] Bildirim denemesi', 'closed', ['o']))
            self.assertIn('@o', w.issues[0]['body'])

    def test_weekly_report_issue_is_opened_then_closed(self):
        with World() as w:
            alerts.post_report('[FORMFORM] Haftalık özet', 'satır 1\n1. ÖZET')
            self.assertEqual(len(w.issues), 1)
            self.assertEqual(w.issues[0]['state'], 'closed')
            self.assertEqual(w.issues[0]['assignees'], ['o'])
            self.assertIn('```text', w.issues[0]['body'])


class Tokens(unittest.TestCase):
    def test_refresh_stores_sealed_token_and_current_uses_it(self):
        with World() as w:
            res = tokens.refresh(w.store(), now=NOW)
            self.assertEqual(res['expires_at'], '2026-12-01T08:59:04Z')
            data = w.store().read()
            self.assertTrue(data.sealed_token)
            self.assertNotIn('IGAA', data.sealed_token)                 # düz metin depoda yok
            self.assertNotIn(TOKEN, data.sealed_token)
            new = w.refresh_response['access_token']
            self.assertEqual(tokens.current(data), new)
            call = w.srv.named('GET', r'/ig/refresh_access_token')[0]
            self.assertEqual((call.query['grant_type'], call.query['access_token']), ('ig_refresh_token', TOKEN))
            # ikinci yenileme artık yenilenmiş jetonla yapılır
            tokens.refresh(w.store(), now=NOW)
            self.assertEqual(w.srv.named('GET', r'/ig/refresh_access_token')[1].query['access_token'], new)

    def test_changing_the_secret_discards_the_stored_token(self):
        with World() as w:
            tokens.refresh(w.store(), now=NOW)
            fresh = 'IGAA' + 'Q9' * 30
            os.environ['IG_ACCESS_TOKEN'] = fresh
            self.assertEqual(tokens.current(w.store().read()), fresh)

    def test_invalid_refresh_response_keeps_old_token(self):
        for bad in ({}, {'access_token': 'kisa', 'expires_in': 5184000},
                    {'access_token': 'IGAA' + 'x' * 50, 'expires_in': 10}, {'error': {'message': 'x'}}):
            with World() as w:
                w.refresh_response = bad
                with self.assertRaises(TokenRefreshFailed) as cm:
                    tokens.refresh(w.store(), now=NOW)
                self.assertNotIn('IGAA', str(cm.exception))
                self.assertEqual(w.store().read().sealed_token, '')
                self.assertEqual(tokens.current(w.store().read()), TOKEN)

    def test_refresh_http_error_keeps_old_token(self):
        with World() as w:
            w.srv.route('GET', r'/ig/refresh_access_token', lambda c, m: (400, {'error': 'süresi dolmuş'}))
            with self.assertRaises(TokenRefreshFailed):
                tokens.refresh(w.store(), now=NOW)
            self.assertEqual(tokens.current(w.store().read()), TOKEN)


class Cli(unittest.TestCase):
    def test_plan_writes_release_json_and_exports_render_id(self):
        with World() as w, tempfile.TemporaryDirectory() as tmp:
            env_file = os.path.join(tmp, 'env')
            os.environ['GITHUB_ENV'] = env_file
            try:
                code, out = cli_run('plan', '--out', tmp)
            finally:
                del os.environ['GITHUB_ENV']
            self.assertEqual(code, 0, out)
            spec = read_json(os.path.join(tmp, 'release.json'))
            self.assertRegex(spec['video_id'], r'^FF_\d{8}_\d{6}$')
            self.assertEqual(spec['loop'], 'seamless')
            self.assertTrue(spec['caption'] and spec['metadata'])
            self.assertIn(f"RENDER_ID={spec['video_id']}", read(env_file))
            # planlama GitHub'dan yalnız ay içi çalışmaları OKUR (bütçe bekçisi), hiçbir şeyi değiştirmez
            self.assertEqual({(c.method, c.path) for c in w.srv.calls}, {('GET', '/gh/repos/o/r/actions/runs')})

    def test_plan_uses_manual_spec_when_given(self):
        with World(), tempfile.TemporaryDirectory() as tmp:
            os.environ['SPEC_IN'] = json.dumps({'video_id': 'FF_ELLE_1', 'structure': 'spiral form'})
            try:
                code, _ = cli_run('plan', '--out', tmp, '--spec-env', 'SPEC_IN')
            finally:
                del os.environ['SPEC_IN']
            self.assertEqual(code, 0)
            self.assertEqual(read_json(os.path.join(tmp, 'release.json'))['video_id'], 'FF_ELLE_1')

    def test_plan_rejects_unsafe_video_id(self):
        with World(), tempfile.TemporaryDirectory() as tmp:
            os.environ['SPEC_IN'] = json.dumps({'video_id': 'x; rm -rf /'})
            try:
                code, out = cli_run('plan', '--out', tmp, '--spec-env', 'SPEC_IN')
            finally:
                del os.environ['SPEC_IN']
            self.assertEqual(code, 1)
            self.assertIn('::error::', out)

    def test_failed_command_exits_nonzero_writes_hint_and_hides_token(self):
        with World([]) as w:                                           # hazır render yok
            code, out = cli_run('publish')
            self.assertEqual(code, 1)
            self.assertIn('::error::Yayınlanacak render yok', out)
            self.assertNotIn(TOKEN, out)
            info = read_json(alerts.error_file())
            self.assertEqual(info['command'], 'publish')
            self.assertIn('FORMFORM render', info['hint'])
            # ardından alert adımı bu bilgiyle issue açar
            code, _ = cli_run('alert', '--workflow', 'FORMFORM publish', '--run-url', 'https://run')
            self.assertEqual(code, 0)
            self.assertIn('Yayınlanacak render yok', w.issues[0]['body'])
            self.assertIn('https://run', w.issues[0]['body'])

    def test_unexpected_exception_is_reported_not_raised(self):
        with World():
            os.environ.pop('IG_ACCESS_TOKEN')
            code, out = cli_run('learn')                               # veri yok → sorun değil
            self.assertEqual(code, 0)
            from unittest import mock
            with mock.patch('ops.learn.run', side_effect=ZeroDivisionError('x')):
                code, out = cli_run('learn')
            self.assertEqual(code, 1)
            self.assertIn('ZeroDivisionError', out)

    def test_dry_run_flag(self):
        with World([release(1)]) as w:
            code, out = cli_run('publish', '--dry-run')
            self.assertEqual(code, 0, out)
            self.assertEqual(w.srv.named('POST', r'/ig/v23.0/\d+/media'), [])

    def test_ping_command(self):
        with World() as w:
            code, out = cli_run('ping')
            self.assertEqual(code, 0, out)
            self.assertEqual(len(w.issues), 1)

    def test_token_command(self):
        with World():
            code, out = cli_run('token')
            self.assertEqual(code, 0, out)
            self.assertIn('Jeton yenilendi', out)
            self.assertNotIn(TOKEN, out)

    def test_report_command_posts_issue(self):
        with World() as w:
            row = {c: '' for c in VIDEO_COLUMNS}
            row.update(video_id='FF_1', instagram_id='M1', published_at='2026-10-01T05:00:00.000Z',
                       performance_score='50', views_72h='100', structure='s', palette='p', loop_strategy='seamless_8s')
            w.store().transaction(lambda d: d.videos.append(row), 'tohum')
            code, out = cli_run('report')
            self.assertEqual(code, 0, out)
            self.assertEqual(len(w.issues), 1)
            self.assertIn('FORMFORM haftalık özet', w.issues[0]['body'])


if __name__ == '__main__':
    unittest.main()
