"""Regression tests for native GUI project creation and ASR evidence filtering."""
from __future__ import annotations
import os
import tempfile
import unittest
import wave
import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from audiobook_qc import worker
from audiobook_qc.offline import resolve_model, offline_environment
from audiobook_qc.desktop import create_project
from audiobook_qc.verification import classify_second_pass, phrase_in, prioritize_rechecks, recheck_issues


class OfflineTests(unittest.TestCase):
    def test_absent_model_cannot_trigger_download(self):
        with tempfile.TemporaryDirectory() as d:
            with patch('audiobook_qc.offline.model_directories',return_value=[Path(d)]):
                with self.assertRaises(FileNotFoundError):resolve_model('medium')

    def test_local_model_verified(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)/'medium';folder.mkdir()
            for filename in ('model.bin','config.json','tokenizer.json'):(folder/filename).write_text('ok')
            with patch('audiobook_qc.offline.model_directories',return_value=[Path(d)]):
                self.assertEqual(resolve_model('medium'),folder)

    def test_environment_is_strictly_offline(self):
        with patch.dict(os.environ,{},clear=False):
            offline_environment()
            self.assertEqual(os.environ['HF_HUB_OFFLINE'],'1')
            self.assertEqual(os.environ['TRANSFORMERS_OFFLINE'],'1')

    def test_direct_project_six_hours_bounded_parts(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            (root/'book.txt').write_text('Сияла луна, а впереди лежала тихая улица. '*60,encoding='utf-8')
            with wave.open(str(root/'audio.wav'),'wb') as f:
                f.setnchannels(1);f.setsampwidth(2);f.setframerate(8000)
                f.writeframes(b'\0\0'*4000)
            with patch.object(worker,'ROOT',root), patch('audiobook_qc.desktop.resolve_model',return_value=Path('unused')), \
                 patch('audiobook_qc.desktop.worker.audio_duration',return_value=6*3600), \
                 patch('audiobook_qc.dual_engine.resolve_secondary_dir',return_value=Path('unused')), \
                 patch('audiobook_qc.events._manifest_verified'):
                pid=create_project(root/'audio.wav',root/'book.txt',20)
                j=worker.load_job(pid)
                self.assertEqual(len(j['parts']),18)
                self.assertEqual(j['model'],'medium')
                self.assertEqual(j['report_policy'],'dual-gigaam')
                self.assertEqual(j['parts'][-1]['end'],6*3600)
                self.assertTrue((root/'projects'/pid/'inputs'/'audio.wav').is_file())


class RecheckTests(unittest.TestCase):
    def example(self):
        return {'id':'case123','kind':'replacement','severity':'high','confidence':'medium',
                'start':10.,'end':11.,'spoken':'в домике оказались','expected':'в городе оказались',
                'reason':'ASR difference'}

    def test_verification_suppresses_when_source_reappears(self):
        x=classify_second_pass(self.example(),'Потом в городе оказались все люди.')
        self.assertEqual(x['severity'],'review')
        self.assertEqual(x['id'],'case123')

    def test_verification_retains_when_mismatch_reappears(self):
        x=classify_second_pass(self.example(),'В самом деле в домике оказались люди.')
        self.assertEqual(x['severity'],'high')
        self.assertEqual(x['confidence'],'medium')

    def test_unknown_alternative_never_claims_confirmed(self):
        x=classify_second_pass(self.example(),'Сильный шум, невозможно разобрать слова')
        self.assertEqual(x['severity'],'review')

    def test_important_negation_not_deleted_before_second_pass(self):
        x=self.example();x.update({'spoken':'они не захотели','expected':'они захотели'})
        self.assertIn(0,prioritize_rechecks([x]))

    def test_phrase_boundary_exact(self):
        self.assertTrue(phrase_in('в городе оказались','а потом в городе оказались они'))
        self.assertFalse(phrase_in('в городе оказались','а потом в городе их не было'))

    def test_failed_second_pass_not_promoted(self):
        issue=self.example()
        with patch('audiobook_qc.verification.tempfile.TemporaryDirectory') as tmp, \
             patch('audiobook_qc.verification.prioritize_rechecks',return_value=[0]):
            tmp.return_value.__enter__.return_value='/tmp'
            result=recheck_issues(object(),Path('fake.mp3'),[issue],150,
                                  lambda *a,**kw: (_ for _ in ()).throw(ValueError('simulated')))
        self.assertEqual(result[0]['severity'],'review')


if __name__=='__main__':unittest.main()

class NewReportPolicyTests(unittest.TestCase):
    def test_only_rechecked_text_mismatches_are_exported_in_new_projects(self):
        from audiobook_qc.report import generate_report
        from docx import Document
        with tempfile.TemporaryDirectory() as folder:
            source={"audio_name":"audio.wav","text_name":"book.txt","report_policy":"second-pass"}
            items=[{"id":"raw","kind":"replacement","severity":"high","start":1,"end":2,
                    "spoken":"мы подошли к мосту","expected":"мы приехали к мосту","reason":"Первый проход"},
                   {"id":"checked","kind":"replacement","severity":"high","rechecked":True,
                    "start":5,"end":6,"spoken":"его отправили домой","expected":"его отправили обратно","reason":"Два прохода"}]
            dest=Path(folder)/'result.docx'
            generate_report(dest,source,items,0,1200)
            doc=Document(dest)
            content=' '.join(c.text for table in doc.tables for row in table.rows for c in row.cells)
            self.assertIn('его отправили домой',content)
            self.assertNotIn('мы подошли к мосту',content)
