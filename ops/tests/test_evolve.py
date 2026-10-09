"""Evrim motoru: ses, hashtag ve açıklama satırı, bütçe kısıtı, ebeveyn mirası.

Burada evrim motorunun davranışı ve sonradan eklenen boyutlar sınanır.
"""
import random
import re
import unittest
from datetime import datetime, timezone

from formform import library as L

from ops import caption as captions
from ops import evolve
from ops.tests.refs import rand_dna, rand_video

NOW = datetime(2026, 10, 3, 3, 30, tzinfo=timezone.utc)


def gen(seed, videos=(), dna=(), **kw):
    r = random.Random(seed)
    return evolve.generate(list(videos), list(dna), r.random, NOW, **kw)


class Fields(unittest.TestCase):
    def test_every_recipe_has_valid_sound_size_and_loop(self):
        seen = set()
        for seed in range(120):
            out = gen(seed)
            spec = out['release_payload']
            self.assertIn(spec['sound'], L.SOUNDS)
            self.assertEqual((spec['width'], spec['height']), (1080, 1920))
            self.assertEqual(spec['loop'], 'seamless')
            self.assertEqual(out['metadata']['sound'], spec['sound'])
            seen.add(spec['sound'])
        self.assertEqual(seen, set(L.SOUNDS), 'her ses stili seçilebilmeli')

    def test_hashtags_are_unique_valid_and_round_trip_through_the_caption(self):
        for seed in range(150):
            out = gen(seed)
            tags = out['metadata']['tags']
            pool = evolve.TAGS_CORE + evolve.PALETTE_TAGS[out['metadata']['palette']]
            self.assertEqual(len(tags), evolve.TAG_COUNT)
            self.assertEqual(len(set(tags)), len(tags))
            for t in tags:
                self.assertTrue(re.fullmatch(r'#[a-z0-9]+', t), t)
                self.assertIn(t, pool)
                self.assertNotEqual(t, '#formform')
            self.assertEqual(captions.tags(out['caption']), tags)

    def test_caption_has_four_calm_blocks_in_a_fixed_order(self):
        for seed in range(150):
            out = gen(seed)
            blocks = out['caption'].split('\n\n')
            self.assertEqual(len(blocks), 4, out['caption'])
            hook, descriptor, signature, tag_line = blocks
            self.assertIn(hook, evolve.CAPTION_OPENINGS)
            self.assertEqual(hook, out['metadata']['hook'])
            self.assertIn(signature, evolve.CAPTION_SECOND_LINES)
            self.assertTrue(tag_line.startswith('#formform '))
            self.assertNotIn('{', out['caption'])               # doldurulmamış şablon alanı kalmadı
            self.assertLess(len(out['caption']), 700)           # Instagram sınırı 2200, kısa ve sakin
            self.assertEqual(captions.hook(out['caption']), hook)

    def test_descriptor_describes_the_actual_video(self):
        for seed in range(80):
            out = gen(seed)
            d = out['caption'].split('\n\n')[1].lower()
            m = out['metadata']
            self.assertIn(m['structure'].lower(), d)
            # renk anılıyorsa videonun kendi paletinin rengidir (bazı şablonlar renk söylemez)
            for palette, words in evolve.COLOR_WORDS.items():
                if palette != m['palette']:
                    self.assertNotIn(words, d, f'{palette} rengi yanlış videoda: {d}')
            m_dur = re.search(r'(\d+)-second|(\d+)s loop', d)
            if m_dur:
                self.assertEqual(int(m_dur.group(1) or m_dur.group(2)), out['release_payload']['duration'])
            if 'sound:' in d:
                self.assertIn(m['sound'], d)

    def test_text_banks_are_clean_and_varied(self):
        self.assertGreaterEqual(len(evolve.CAPTION_OPENINGS), 40)
        self.assertGreaterEqual(len(evolve.CAPTION_SECOND_LINES), 20)
        self.assertGreaterEqual(len(evolve.TAGS_CORE), 20)
        for line in evolve.CAPTION_OPENINGS + evolve.CAPTION_SECOND_LINES:
            self.assertEqual(line, line.strip())
            self.assertNotIn('\n', line)
        for t in evolve.TAGS_CORE + [t for v in evolve.PALETTE_TAGS.values() for t in v]:
            self.assertRegex(t, r'^#[a-z0-9]+$')
        # çeşitlilik: 300 videoda çok sayıda farklı açılış ve hashtag seti
        hooks, sets, descs = set(), set(), set()
        for seed in range(300):
            out = gen(seed)
            hooks.add(out['metadata']['hook'])
            sets.add(tuple(sorted(out['metadata']['tags'])))
            descs.add(out['caption'].split('\n\n')[1])
        self.assertGreater(len(hooks), 40)
        self.assertGreater(len(sets), 250)
        self.assertGreater(len(descs), 200)

    def test_all_templates_fill_without_error(self):
        for template in evolve.DESCRIPTOR_TEMPLATES:
            text = template.format(S='Floating core', s='floating core', colors='red light', Colors='Red light',
                                   dur=8, sound='airy pad')
            self.assertNotIn('{', text)

    def test_deterministic_for_the_same_random_sequence(self):
        self.assertEqual(gen(7), gen(7))
        self.assertNotEqual(gen(7)['caption'], gen(8)['caption'])


class BudgetConstraints(unittest.TestCase):
    def test_formats_and_size_are_respected(self):
        for seed in range(100):
            out = gen(seed, formats=['seamless_8s'], size=(720, 1280))
            spec = out['release_payload']
            self.assertEqual((spec['duration'], spec['fps'], len(spec['transformations'])), (8, 30, 4))
            self.assertEqual(out['metadata']['format'], 'seamless_8s')
            self.assertEqual((spec['width'], spec['height']), (720, 1280))

    def test_unrestricted_uses_all_formats(self):
        formats = {gen(seed)['metadata']['format'] for seed in range(100)}
        self.assertEqual(formats, set(evolve.FORMAT_NAMES))

    def test_elite_parent_with_a_disallowed_format_does_not_force_it(self):
        parent = rand_video(random.Random(1), 1)
        parent.update(loop_strategy='seamless_30s', performance_score='90')
        history = [dict(parent, video_id=f'FF_{i}') for i in range(6)]
        for seed in range(60):
            out = gen(seed, videos=history, formats=['seamless_8s'])
            self.assertEqual(out['metadata']['format'], 'seamless_8s')


class Inheritance(unittest.TestCase):
    @staticmethod
    def stub_rng(*first):
        """İlk çekimler verilen, sonrası hep 0,99 (mutasyon yok): ebeveyn genleri korunur."""
        seq = list(first)

        def rng():
            return seq.pop(0) if seq else 0.99
        return rng

    def test_elite_child_inherits_sound_and_hashtags_when_nothing_mutates(self):
        rng = random.Random(3)
        history = []
        for i in range(6):
            v = rand_video(rng, i)
            v.update(video_id=f'FF_{i}', performance_score=str(60 + i), sound='metal shimmer',
                     caption='A temporary geometry.\n\nx\n\nform/form — drift.\n\n#formform #lightart #hypnotic '
                             '#loopart #darkaesthetic', loop_strategy='seamless_8s')
            history.append(v)
        # 0.9: exploitation, 0.1: elit melezleme, 0.99: rulet son ebeveyni seçer, sonrası 0,99: mutasyon yok
        out = evolve.generate(history, [], self.stub_rng(0.9, 0.1), NOW)
        self.assertEqual(out['metadata']['evolution'], 'elite')
        self.assertEqual(out['metadata']['sound'], 'metal shimmer')
        self.assertEqual(out['metadata']['tags'], ['#lightart', '#hypnotic', '#loopart', '#darkaesthetic'])
        self.assertEqual(out['metadata']['hook'], 'A temporary geometry.')

    def test_weighted_choice_follows_learned_sound_and_tag_weights(self):
        dna = [{'dimension': 'sound', 'value': 'sub pulse', 'weight': '1.8'}]
        dna += [{'dimension': 'sound', 'value': s, 'weight': '0.6'} for s in L.SOUNDS if s != 'sub pulse']
        dna += [{'dimension': 'tag', 'value': '#lightart', 'weight': '1.8'}]
        counts, tag_hits, n = {}, 0, 0
        for seed in range(400):
            out = gen(seed, dna=dna)
            if out['metadata']['selection_mode'] != 'exploitation':
                continue
            n += 1
            counts[out['metadata']['sound']] = counts.get(out['metadata']['sound'], 0) + 1
            tag_hits += '#lightart' in out['metadata']['tags']
        self.assertGreater(counts['sub pulse'] / n, 0.35)          # tek başına 1/5'ten (0,20) çok seçilir
        self.assertGreater(tag_hits / n, 4 / 24 * 1.2)             # ağırlıksız beklenen oranın üstünde

    def test_exploration_ignores_weights(self):
        dna = [{'dimension': 'sound', 'value': 'sub pulse', 'weight': '1.8'}]
        picks = set()
        for seed in range(300):
            out = gen(seed, dna=dna)
            if out['metadata']['selection_mode'] == 'exploration':
                picks.add(out['metadata']['sound'])
        self.assertEqual(picks, set(L.SOUNDS))


class OldFieldsStayStable(unittest.TestCase):
    def test_new_draws_come_last(self):
        """Eski alanlar, sonradan eklenen ses/hashtag/açıklama çekimlerinden etkilenmez: aynı dizinin ilk kısmı
        aynı yapı, palet, hareket ve ışığı verir (eski kodla eşitlik test_parity.py'de)."""
        rng = random.Random(21)
        base = [rng.random() for _ in range(300)]
        a, b = iter(base), iter(base[:60] + [0.123] * 240)       # ilk 60 çekim aynı, sonrası farklı
        x = evolve.generate([], [], lambda: next(a), NOW)
        y = evolve.generate([], [], lambda: next(b), NOW)
        for key in ('structure', 'palette', 'motion_quality', 'lighting', 'format', 'transformations', 'materials',
                    'hook'):
            self.assertEqual(x['metadata'][key], y['metadata'][key], key)


if __name__ == '__main__':
    unittest.main()
