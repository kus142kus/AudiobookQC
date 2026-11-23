"""Regression tests: the modern HTML window is local and preserves project APIs."""
from __future__ import annotations
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from audiobook_qc.desktop_web import Bridge, bundled_html, web_assets
from audiobook_qc import worker


class DesktopWebViewTests(unittest.TestCase):
    def test_assets_are_bundled_without_http_server(self):
        html = bundled_html()
        self.assertIn('id="projectView"',html)
        self.assertIn('id="timeline"',html)
        self.assertIn('id="partsGrid"',html)
        self.assertIn('overflow-y:auto',html)
        self.assertNotIn('http://127.0.0.1',html)
        self.assertNotIn('https://',html)
        self.assertTrue(web_assets().is_dir())

    def test_safe_filtered_project_read(self):
        job = {'id':'a'*32,'audio_name':'book.mp3','text_name':'book.docx',
               'duration':7200,'processed_seconds':1200,'part_minutes':20,
               'model':'medium','report_policy':'dual-gigaam','status':'paused',
               'parts':[{'index':1,'start':0,'end':1200,'status':'done','issue_count':2}]}
        issues = [dict(id='1',kind='replacement',start=10,end=11,spoken='доме',expected='городе',
                       reason='Возможное расхождение',severity='high',dual_confirmed=True),
                  dict(id='2',kind='replacement',start=50,end=51,spoken='доме',expected='городе',
                       reason='Расхождение',severity='high',dual_confirmed=False)]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(worker,'ROOT',root):
                folder = worker.job_dir('a'*32)
                folder.mkdir(parents=True)
                (folder/'reviews.json').write_text('{}',encoding='utf-8')
                with patch.object(worker,'load_job',return_value=job),patch.object(worker,'all_issues',return_value=issues):
                    result=Bridge().get_project('a'*32)
                    self.assertEqual(result['duration_label'],'02:00:00')
                    self.assertEqual(len(result['issues']),1)
                    self.assertEqual(len(Bridge().get_project('a'*32,True)['issues']),2)

    def test_bad_speed_refused(self):
        b=Bridge()
        with self.assertRaises(ValueError): b.set_speed(4)

if __name__ == '__main__': unittest.main()
