"""Evrim motorunun listeleri render kütüphanesinden okunur: yeni eklenen ad kendiliğinden üretilebilir olur."""
import unittest

from formform import library as L

from ops import evolve


class LibrarySync(unittest.TestCase):
    def test_evolve_lists_are_the_render_library(self):
        self.assertEqual(evolve.STRUCTURES, list(L.STRUCTURES))
        self.assertEqual(evolve.PALETTES, list(L.PALETTES))
        self.assertEqual(evolve.MATERIALS, list(L.MATERIALS))
        self.assertEqual(evolve.TRANSFORMATIONS, list(L.TRANSFORMATIONS))
        self.assertEqual(evolve.MOTION_QUALITIES, list(L.MOTIONS))
        self.assertEqual(evolve.LIGHTING_STYLES, list(L.LIGHTING))
        self.assertEqual(evolve.SOUND_NAMES, list(L.SOUNDS))

    def test_no_duplicates_and_every_palette_has_hashtags(self):
        for name in ('STRUCTURES', 'PALETTES', 'MATERIALS', 'TRANSFORMATIONS', 'MOTION_QUALITIES', 'LIGHTING_STYLES',
                     'CAPTION_OPENINGS', 'CAPTION_SECOND_LINES', 'FORMAT_NAMES'):
            values = getattr(evolve, name)
            self.assertEqual(len(values), len(set(values)), name)
        self.assertEqual(set(evolve.PALETTE_TAGS), set(evolve.PALETTES))
        self.assertEqual(set(evolve.COLOR_WORDS), set(evolve.PALETTES))
        for name in ('TAGS_CORE', 'DESCRIPTOR_TEMPLATES', 'SOUND_NAMES'):
            values = getattr(evolve, name)
            self.assertEqual(len(values), len(set(values)), name)

    def test_caption_parts_never_break_the_learning_split(self):
        """hook = caption'ın ilk satırı. Açılış cümlesi satır sonu ya da boşluk kenarı içermemeli."""
        for line in evolve.CAPTION_OPENINGS:
            self.assertEqual(line, line.strip())
            self.assertNotIn('\n', line)

    def test_format_table_matches_the_documented_formats(self):
        self.assertEqual([(f['name'], f['duration'], f['fps'], f['transformations']) for f in evolve.FORMATS],
                         [('seamless_8s', 8, 30, 4), ('seamless_15s', 15, 30, 7), ('seamless_30s', 30, 24, 10)])


if __name__ == '__main__':
    unittest.main()
