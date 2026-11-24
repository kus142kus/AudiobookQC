"""Regression checks for clear, reviewable editorial DOCX exports."""
import sys
import tempfile
import unittest
from pathlib import Path
from docx import Document
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from audiobook_qc.report import generate_report, _build_doc

class EditorialReportTests(unittest.TestCase):
    def test_actionable_sections_and_separate_sound_review(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/'Часть_001.docx'
            issues = [
                dict(id='a',kind='replacement',start=324,end=325,spoken='доме',expected='городе',
                     severity='high',rechecked=True,dual_confirmed=True,dual_checked=True,reason='Whisper + GigaAM'),
                dict(id='b',kind='replacement',start=621,end=622,spoken='мной',expected='мною',
                     severity='high',rechecked=True,dual_confirmed=True,dual_checked=True,reason='variant'),
                dict(id='c',kind='sound_event',start=771,end=772,spoken='—',expected='—',
                     severity='review',reason='Модель предположила кашель (0.43)')]
            generate_report(path, {'audio_name':'16_01.mp3','text_name':'16_01.docx','report_policy':'dual-gigaam'},issues,0,1200,
                            reviews={'a':'todo','b':'fixed'})
            doc=Document(path)
            paras='\n'.join(p.text for p in doc.paragraphs)
            cells='\n'.join(cell.text for table in doc.tables for row in table.rows for cell in row.cells)
            self.assertIn('Небольшие редакционные отличия', paras)
            self.assertIn('ТОЛЬКО ДЛЯ ПРОСЛУШИВАНИЯ', paras)
            self.assertIn('00:05:24', cells)
            self.assertIn('00:10:21', cells)
            self.assertIn('00:12:51', cells)
            self.assertIn('Исправлено', cells)
            self.assertIn('кашель', cells)
            self.assertTrue(path.stat().st_size > 5000)

    def test_empty_report_has_no_fake_issues(self):
        d=_build_doc('Проверка озвучки','Часть · 00:00:00—00:20:00',[],
                    {'audio_name':'a.mp3','text_name':'a.docx','range':'00:00:00–00:20:00','total':0})
        text=' '.join(p.text for p in d.paragraphs)
        self.assertIn('нет',text.lower())
        self.assertNotIn('В ПЕРВУЮ ОЧЕРЕДЬ',text)

    def test_lengthy_explanations_are_bounded(self):
        doc=_build_doc('Проверка озвучки','Часть 01',[],
                       {'audio_name':'a.mp3','text_name':'a.docx','range':'00:00:00–00:20:00','total':1},
                       advisory=[{'id':'sound', 'kind':'hiss', 'start':2,'end':4,'reason':'Шум'*2000}])
        texts=' '.join(cell.text for table in doc.tables for row in table.rows for cell in row.cells)
        self.assertLess(len(texts), 1600)

if __name__ == '__main__':
    unittest.main()

class VisibleGridAndReportSemantics(unittest.TestCase):
    def test_grid_borders_and_repeating_header_rows(self):
        from docx.oxml.ns import qn
        d = _build_doc('Проверка озвучки','Часть',[
            dict(id='a',kind='replacement',start=10,spoken='дом',expected='сад',review='todo')],
            {'audio_name':'a.mp3','text_name':'b.docx','range':'0–20','total':1})
        self.assertGreaterEqual(len(d.tables),2)
        for t in d.tables:
            borders=t._tbl.tblPr.find(qn('w:tblBorders'))
            self.assertIsNotNone(borders)
            for edge in ('top','bottom','left','right','insideH','insideV'):
                self.assertEqual(borders.find(qn('w:'+edge)).get(qn('w:val')),'single')
            self.assertIsNotNone(t.rows[0]._tr.get_or_add_trPr().find(qn('w:tblHeader')))

    def test_clipping_never_duplicated_in_reading_section(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'c.docx'
            generate_report(p,{'audio_name':'a','text_name':'b'},[
                dict(id='clip',kind='clipping',start=3,end=5,spoken='—',expected='—',
                     severity='high',reason='near 0 dBFS')],0,60)
            d=Document(p)
            alltext=' '.join(p.text for p in d.paragraphs)+' '.join(
                cell.text for t in d.tables for r in t.rows for cell in r.cells)
            self.assertEqual(alltext.count('Возможная перегрузка'),1)
            self.assertIn('ЗВУК, УДАРЕНИЯ И ИНТОНАЦИЯ',alltext)

    def test_long_expected_and_spoken_cells_bounded(self):
        d=_build_doc('Проверка озвучки','Часть',[
            dict(id='a',kind='omission',start=33,expected='длинное '*500,
                 spoken='прочитано '*500)],
            {'audio_name':'a.mp3','text_name':'b.docx','range':'0–20'})
        cells=[c.text for t in d.tables for r in t.rows for c in r.cells]
        self.assertLess(max(map(len,cells)), 230)

    def test_original_and_recognized_columns_not_swapped(self):
        d=_build_doc('Проверка озвучки','Часть',[
            dict(id='a',kind='replacement',start=10,expected='городе',spoken='доме')],
            {'audio_name':'a.mp3','text_name':'b.docx','range':'0–20'})
        table=d.tables[1]
        self.assertEqual(table.rows[1].cells[1].text,'городе')
        self.assertEqual(table.rows[1].cells[2].text,'доме')

    def test_editorial_variation_not_mixed_with_major_errors(self):
        d=_build_doc('Проверка озвучки','Часть',[
            dict(id='a',kind='replacement',start=10,expected='мною',spoken='мной'),
            dict(id='b',kind='replacement',start=11,expected='городе',spoken='доме')],
            {'audio_name':'a.mp3','text_name':'b.docx','range':'0–20'})
        text=' '.join(p.text for p in d.paragraphs)
        self.assertIn('Редакционные варианты: 1',text)
        self.assertIn('Основные находки: 1',text)
