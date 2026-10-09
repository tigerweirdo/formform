"""Ses stili ve hashtag'ler gerçekten öğreniliyor mu? (Göç sonrası eklenen iki boyut.)"""
import unittest

from ops import caption, scoring
from ops.store import VIDEO_COLUMNS


def row(i, score, sound, tags, hook='A temporary geometry.', published='2026-10-0'):
    r = {c: '' for c in VIDEO_COLUMNS}
    r.update(video_id=f'FF_{i}', instagram_id=f'M{i}', published_at=f'{published}{1 + i % 9}T05:00:00.000Z',
             performance_score=str(score), structure='spiral form', palette='violet-pink', motion_quality='m',
             lighting='l', loop_strategy='seamless_8s', materials='a, b', transformations='t1',
             sound=sound, caption=f'{hook}\n\nDescriptor line.\n\nform/form — drift.\n\n#formform ' + ' '.join(tags))
    return r


class CaptionParts(unittest.TestCase):
    def test_hook_and_tags(self):
        text = 'Open, close, open.\n\nWhat it is.\n\nform/form — drift.\n\n#formform #lightart #hypnotic #loopart'
        self.assertEqual(caption.hook(text), 'Open, close, open.')
        self.assertEqual(caption.tags(text), ['#lightart', '#hypnotic', '#loopart'])

    def test_edge_cases(self):
        self.assertEqual(caption.tags(''), [])
        self.assertEqual(caption.tags(None), [])
        self.assertEqual(caption.tags('only a hook'), [])
        self.assertEqual(caption.hook(None), '')
        self.assertEqual(caption.tags('x\n\n#formform'), [])          # #formform her videoda var, bir şey öğretmez
        self.assertEqual(caption.tags('x\n\n#formform #a #a  #b\n'), ['#a', '#a', '#b'])


class LearnedDimensions(unittest.TestCase):
    def weights(self, rows):
        return {w['key']: w for w in scoring.dna_weights(rows, 'stamp')}

    def test_sound_and_each_hashtag_become_genes(self):
        rows = [row(i, 80, 'airy pad', ['#lightart', '#hypnotic']) for i in range(4)]
        rows += [row(i + 4, 20, 'sub pulse', ['#hypnotic', '#loopart']) for i in range(4)]
        w = self.weights(rows)
        self.assertEqual(w['sound::airy pad']['sample_count'], 4)
        self.assertEqual(w['sound::sub pulse']['sample_count'], 4)
        self.assertEqual(w['tag::#hypnotic']['sample_count'], 8)       # iki gruba da girer
        self.assertEqual(w['tag::#lightart']['sample_count'], 4)
        self.assertNotIn('tag::#formform', w)
        # iyi performans gösteren ses ve hashtag daha yüksek ağırlık alır
        self.assertGreater(w['sound::airy pad']['weight'], w['sound::sub pulse']['weight'])
        self.assertGreater(w['tag::#lightart']['weight'], w['tag::#loopart']['weight'])

    def test_a_repeated_hashtag_counts_once_per_video(self):
        w = self.weights([row(1, 50, 'airy pad', ['#a', '#a', '#b'])])
        self.assertEqual(w['tag::#a']['sample_count'], 1)

    def test_old_rows_without_sound_do_not_create_a_blank_gene(self):
        w = self.weights([row(1, 50, '', ['#a'])])
        self.assertFalse([k for k in w if k.startswith('sound::')])
        self.assertIn('tag::#a', w)

    def test_dimensions_are_the_ones_the_report_and_evolution_know(self):
        from ops import report
        dims = {w['dimension'] for w in scoring.dna_weights([row(1, 50, 'airy pad', ['#a'])], 's')}
        self.assertTrue({'sound', 'tag', 'hook', 'structure'} <= dims)
        self.assertTrue({'sound', 'tag'} <= set(report.NAMES))


if __name__ == '__main__':
    unittest.main()
