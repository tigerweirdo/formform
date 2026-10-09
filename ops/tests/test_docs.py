"""Root README.md should stay aligned with code and workflows."""
import glob
import json
import os
import re
import unittest

from ops.tests.test_workflows_yaml import OPS, SECRETS, load, triggers

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
README = 'README.md'


def read(name):
    with open(os.path.join(ROOT, name), encoding='utf-8') as f:
        return f.read()


def source():
    out = ''
    for path in glob.glob(os.path.join(ROOT, 'ops', '*.py')):
        with open(path, encoding='utf-8') as f:
            out += f.read()
    return out


class ReadmeStructure(unittest.TestCase):
    def test_core_sections_exist(self):
        text = read(README)
        for heading in ('# FORM/FORM', '## What it does', '## Architecture', '## Notable engineering',
                        '## Repository layout', '## Run tests locally', 'formformstudio'):
            self.assertIn(heading, text, heading)

    def test_instagram_link(self):
        self.assertIn('https://www.instagram.com/formformstudio', read(README))

    def test_documents_evolution_rates(self):
        from ops import evolve, scoring
        text = read(README)
        self.assertIn('30%', text)
        self.assertEqual(evolve.EXPLORATION_RATE, 0.30)
        self.assertEqual(evolve.ELITE_RATE, 0.50)
        self.assertEqual(scoring.PRIOR_STRENGTH, 5)
        for fmt in evolve.FORMAT_NAMES:
            self.assertIn(fmt, text, fmt)

    def test_no_live_token_patterns(self):
        text = read(README)
        self.assertNotRegex(text, r'github_pat_[A-Za-z0-9_]{20,}')
        self.assertNotRegex(text, r'IGAA[A-Za-z0-9]{30,}')


class ReadmeOperations(unittest.TestCase):
    def setUp(self):
        self.doc = read(README)

    def test_workflow_names_mentioned(self):
        for key in OPS:
            self.assertIn(key, self.doc, key)

    def test_secrets_listed_match_workflows(self):
        used = set().union(*SECRETS.values())
        for name in sorted(used):
            if name.startswith(('IG_', 'CLOUDINARY_')):
                self.assertIn(name, self.doc, name)

    def test_schedule_berlin(self):
        from ops.schedule import JOBS
        self.assertIn('Europe/Berlin', self.doc)
        for key in ('render', 'publish'):
            for label in JOBS[key]['local']:
                self.assertIn(label, self.doc, f'{key} {label}')

    def test_data_branch_named(self):
        from ops import config
        self.assertIn(config.DATA_BRANCH, self.doc)
        self.assertIn('videos.csv', self.doc)
        self.assertIn('dna_weights.csv', self.doc)

    def test_smoke_spec_file_exists(self):
        path = os.path.join(ROOT, 'render', 'ornek-spec.json')
        with open(path, encoding='utf-8') as f:
            spec = json.load(f)
        try:
            from formform.engine import normalize_spec
            from formform.timeline import Plan
        except ImportError:
            self.skipTest('numpy missing')
        self.assertEqual(Plan(normalize_spec(spec)).warnings, [])

    def test_test_spec_skipped_by_publish(self):
        from ops import publish
        from ops.tests.fakes import release
        r = release(1)
        r['body'] = json.dumps({'video_id': 'FF_TEST_001', 'structure': 'central ring'})
        self.assertFalse(publish.pick_ready([r])['ready'])


if __name__ == '__main__':
    unittest.main()
