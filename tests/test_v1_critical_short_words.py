"""Regression gates based on an actual three-hour audiobook first report.

No copyrighted audio or manuscript is committed to the repository.
"""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from audiobook_qc.analysis import low_value_difference, effective_severity
from audiobook_qc.dual_engine import classify_independent, verify_with_gigaam
from audiobook_qc.report import generate_report
from docx import Document


class ShortMeaningChanges(unittest.TestCase):
    def issue(self, *, spoken='дым', expected='дом'):
        return {
            'id': 'short-meaning', 'kind': 'replacement', 'severity': 'review',
            'spoken': spoken, 'expected': expected,
            'before_anchor': 'он снова вошёл в',
            'after_anchor': 'и потом уснул',
            'start': 10.5, 'end': 10.9,
        }

    def test_distinct_short_words_are_not_discarded_before_independent_check(self):
        for heard, written in [('дым','дом'), ('тир','мир'), ('она','он'), ('три','два')]:
            with self.subTest(heard=heard, written=written):
                self.assertFalse(low_value_difference(heard, written))

    def test_spelling_and_numbers_still_deprioritized(self):
        for heard,written in [('Пятьсот','500'), ('её','ее'), ('голубовато прозрачном','голубовато-прозрачном')]:
            with self.subTest(heard=heard,written=written):
                self.assertTrue(low_value_difference(heard,written))

    def test_single_short_word_needs_second_model_with_context(self):
        issue=self.issue()
        output = classify_independent(issue, 'он снова вошёл в дым и потом уснул')
        self.assertTrue(output.get('dual_confirmed'))
        self.assertEqual(effective_severity(output),'high')

    def test_without_matching_context_a_short_word_is_unconfirmed(self):
        output=classify_independent(self.issue(), 'на улице был дым и запах костра')
        self.assertFalse(output.get('dual_confirmed',False))
        self.assertEqual(effective_severity(output),'review')

    def test_short_word_gets_recheck_even_if_long_candidates_fill_budget(self):
        # The old queue could spend all 18 slots on long phrases and never
        # independently inspect a change to a short, meaningful word.
        many = []
        for j in range(25):
            many.append({'id':f'long-{j}', 'kind':'omission', 'severity':'high',
                         'start':40+j,'end':41+j,
                         'expected':'Это очень длинная значимая пропущенная фраза',
                         'spoken':'—','before_anchor':'в тёмном доме жил',
                         'after_anchor':'и потом он ушёл'})
        short=self.issue()
        with tempfile.TemporaryDirectory() as d:
            output=verify_with_gigaam(object(), Path(d)/'input.wav',
                many + [short], 500,
                lambda audio,start,length,target:target.write_bytes(b'fake'),
                decoder=lambda model,clip:'он снова вошёл в дым и потом уснул',budget=18)
        self.assertTrue(output[-1].get('dual_checked'), 'Meaning-changing short word was starved by long candidates')

    def test_verification_pipeline_rechecks_short_meaning_change(self):
        with tempfile.TemporaryDirectory() as d:
            result=verify_with_gigaam(object(),Path(d)/'input.wav',[self.issue()],1000,
                lambda audio,start,length,target: target.write_bytes(b'test'),
                decoder=lambda model,clip: 'он снова вошёл в дым и потом уснул')
        self.assertTrue(result[0].get('dual_confirmed'))


class HonestReportMetadata(unittest.TestCase):
    def test_unchecked_not_mislabeled_as_failed(self):
        checked={**ShortMeaningChanges().issue(), 'id':'checked',
                 'dual_checked':True,'severity':'review','rechecked':True}
        unchecked={**ShortMeaningChanges().issue(), 'id':'unchecked',
                   'start':15,'end':16,'dual_checked':False}
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/'part.docx'
            generate_report(out, {'audio_name':'book.mp3', 'text_name':'text.docx',
                                  'report_policy':'dual-gigaam'},
                            [checked, unchecked],0,1200)
            text=' '.join(p.text for p in Document(out).paragraphs)
        self.assertIn('Не проверялись второй речевой моделью: 1',text)
        self.assertIn('Проверены, но не получили независимого подтверждения: 1',text)
        self.assertNotIn('Не прошли вторую речевую модель',text)

if __name__ == '__main__':
    unittest.main()
