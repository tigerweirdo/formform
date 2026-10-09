"""Motor kütüphanesi (yapı, palet, malzeme, dönüşüm, hareket, ışık) tek kaynaktır.

`ops/evolve.py` listeleri buradan okur. Burada eklenen yeni bir ad otomatik olarak üretilebilir olur.
Bu test, her adın render motorunda yedeğe düşmeden çözüldüğünü ve ad kurallarını doğrular.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, 'render'))
from formform import library as L          # noqa: E402
from formform.timeline import Plan         # noqa: E402


class Library(unittest.TestCase):
    def test_every_combination_resolves_without_fallback(self):
        """Her yapı/malzeme/dönüşüm evrim motorunun üretebileceği biçimde çözülür, uyarı çıkmaz."""
        for i, st in enumerate(sorted(L.STRUCTURES)):
            spec = dict(video_id=f'FF_T{i}', structure=st, palette=sorted(L.PALETTES)[i % 6],
                        motion_quality=sorted(L.MOTIONS)[i % 6], lighting=sorted(L.LIGHTING)[i % 5],
                        materials=sorted(L.MATERIALS)[:5], transformations=sorted(L.TRANSFORMATIONS)[i:i + 4])
            self.assertEqual(Plan(spec).warnings, [])

    def test_list_names_never_contain_the_list_separator(self):
        """videos.csv'de materials/transformations ', ' ile birleştirilir ve öğrenme bu ayırıcıyla böler.
        Bir ad ', ' içerirse iki ayrı değere bölünür ve ağırlıklar bozulur."""
        for table in (L.MATERIALS, L.TRANSFORMATIONS):
            for name in table:
                self.assertNotIn(', ', name, name)

    def test_knob_references_are_valid(self):
        for name, ops in L.TRANSFORMATIONS.items():
            for knob, op, _ in ops:
                self.assertIn(knob, L.KNOBS, f'{name}: bilinmeyen düğme {knob}')
                self.assertIn(op, ('mul', 'add'))
        for name, s in L.STRUCTURES.items():
            for knob in s:
                self.assertIn(knob, L.KNOBS, f'{name}: bilinmeyen düğme {knob}')
        for name, m in L.MATERIALS.items():
            self.assertEqual(sorted(m), sorted(L.STYLE_KEYS), name)


if __name__ == '__main__':
    unittest.main()
