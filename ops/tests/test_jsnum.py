"""JavaScript'le aynı sayı davranışı ve Python sürümünden bağımsızlık.

Bir hata yalnız GitHub'ın Python 3.12'sinde görüldü: 3.12'de sum() ondalıkları farklı toplar. Bu dosya o sınıfı kapatır.
"""
import ast
import json
import math
import os
import random
import shutil
import subprocess
import unittest

from ops import jsnum

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FoldSum(unittest.TestCase):
    def test_is_plain_left_to_right_like_js_reduce(self):
        # JS: [0.1,...x10].reduce((s,v)=>s+v, 0) === 0.9999999999999999. Python 3.12 sum() 1.0 verir.
        self.assertEqual(jsnum.fold_sum([0.1] * 10), 0.9999999999999999)
        self.assertEqual(jsnum.fold_sum([]), 0)
        self.assertEqual(jsnum.fold_sum(x for x in (1, 2, 3.5)), 6.5)

    def test_ops_code_never_calls_builtin_sum(self):
        offenders = []
        for fn in sorted(os.listdir(os.path.join(ROOT, 'ops'))):
            if not fn.endswith('.py'):
                continue
            with open(os.path.join(ROOT, 'ops', fn), encoding='utf-8') as f:
                tree = ast.parse(f.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'sum':
                    offenders.append(f'ops/{fn}:{node.lineno}')
        self.assertEqual(offenders, [], 'sum() Python sürümüne göre farklı sonuç verir: jsnum.fold_sum kullanın')


class JsSemantics(unittest.TestCase):
    def test_round_half_up_like_math_round(self):
        for x, want in ((0.5, 1), (1.5, 2), (2.5, 3), (-0.5, 0), (-1.5, -1), (-2.5, -2), (2.4999, 2), (409.5, 410)):
            self.assertEqual(jsnum.js_round(x), want, x)

    def test_to_fixed_like_number_to_fixed(self):
        cases = ((2.5, 0, '3'), (0.125, 2, '0.13'), (-1.5, 0, '-2'), (1.005, 2, '1.00'), (10.0, 1, '10.0'),
                 (0.0, 1, '0.0'), (-0.04, 1, '-0.0'), (99.95, 1, '100.0'), (1234.5678, 2, '1234.57'))
        for x, d, want in cases:
            self.assertEqual(jsnum.to_fixed(x, d), want, (x, d))
        self.assertEqual(jsnum.to_fixed(math.nan, 1), 'NaN')

    @unittest.skipUnless(shutil.which('node'), 'node yok')
    def test_to_fixed_and_round_match_node_for_many_values(self):
        rng = random.Random(1)
        vals = [2.5, 0.125, -1.5, 1.005, 10.0, 0.0, -0.04, -0.004, 99.95, 1234.5678, 0.5, 1.45, -2.5, 8.345,
                123456789.125, -0.5, 0.05, -0.05]
        vals += [rng.uniform(-100, 100) for _ in range(300)]
        vals += [round(rng.uniform(-50, 50), rng.randint(0, 3)) for _ in range(300)]
        js = ("const v=JSON.parse(require('fs').readFileSync(0,'utf8'));const o=[];"
              "for(const x of v){for(const d of [0,1,2,3]){o.push(x.toFixed(d))}o.push(Math.round(x))}"
              "console.log(JSON.stringify(o))")
        out = json.loads(subprocess.run(['node', '-e', js], input=json.dumps(vals), capture_output=True,
                                        text=True, check=True).stdout)
        i = 0
        for x in vals:
            for d in (0, 1, 2, 3):
                self.assertEqual(jsnum.to_fixed(x, d), out[i], (x, d))
                i += 1
            self.assertEqual(jsnum.js_round(x), out[i], x)
            i += 1

    def test_number_conversion(self):
        self.assertEqual(jsnum.num(''), 0.0)
        self.assertEqual(jsnum.num('  5 '), 5.0)
        self.assertEqual(jsnum.num('1e3'), 1000.0)
        self.assertTrue(math.isnan(jsnum.num('abc')))
        self.assertTrue(math.isnan(jsnum.num(None)))
        self.assertTrue(math.isnan(jsnum.num('1,5')))
        self.assertEqual(jsnum.num_or0(None), 0.0)
        self.assertEqual(jsnum.num_or0('abc'), 0.0)
        self.assertEqual(jsnum.num_or0('7'), 7.0)

    def test_tidy_and_blank_and_time(self):
        self.assertEqual(jsnum.tidy(5.0), 5)
        self.assertIsInstance(jsnum.tidy(5.0), int)
        self.assertEqual(jsnum.tidy(5.5), 5.5)
        self.assertTrue(jsnum.blank('') and jsnum.blank('  ') and jsnum.blank(None))
        self.assertFalse(jsnum.blank('0') or jsnum.blank(0))
        self.assertEqual(jsnum.parse_time_ms('2026-10-02T05:00:07.123Z'), 1790917207123.0)
        self.assertEqual(jsnum.parse_time_ms('2026-10-02T08:00:07.123+03:00'), 1790917207123.0)
        self.assertTrue(math.isnan(jsnum.parse_time_ms('dün')))
        self.assertTrue(math.isnan(jsnum.parse_time_ms('')))


if __name__ == '__main__':
    unittest.main()
