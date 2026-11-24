"""Regression coverage for the author's day-to-day offline editorial workflow.

All tests use tiny synthetic metadata and files; no user audio, no ASR models.
"""
from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))

from audiobook_qc.desktop_web import Bridge, discover_input_pair
from audiobook_qc.report import generate_report
from docx import Document
import benchmark_readings as benchmark


class FolderImportTests(unittest.TestCase):
    def test_one_audio_one_docx_with_unrelated_files(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder/'Глава 8.m4a').write_bytes(b'audio')
            (folder/'Текст главы 8.docx').write_bytes(b'text')
            (folder/'.DS_Store').write_bytes(b'metadata')
            result = discover_input_pair(folder)
            self.assertEqual(Path(result['audio']).name, 'Глава 8.m4a')
            self.assertEqual(Path(result['text']).name, 'Текст главы 8.docx')

    def test_ambiguous_pair_requires_manual_selection_not_guess(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            for name in ('a.mp3', 'b.mp3', 'book.txt'):
                (folder/name).write_bytes(b'x')
            with self.assertRaisesRegex(ValueError, 'аудиофайлов: 2'):
                discover_input_pair(folder)

    def test_nested_books_not_auto_selected(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder/'chapter').mkdir()
            (folder/'chapter'/'a.wav').write_bytes(b'x')
            (folder/'chapter'/'b.txt').write_bytes(b'x')
            with self.assertRaisesRegex(ValueError, 'аудиофайлов: 0'):
                discover_input_pair(folder)

    def test_cancel_dialog_does_not_modify_selections(self):
        bridge = Bridge()
        class Window:
            def create_file_dialog(self, *_args, **_kwargs):
                return None
        bridge.window = Window()
        with patch.dict(sys.modules, {'webview': type('W', (), {'FOLDER_DIALOG': object()})()}):
            self.assertIsNone(bridge.pick_folder())


class EditorialCorrectionTests(unittest.TestCase):
    def candidate(self, issue_id, start, kind='replacement'):
        return dict(id=issue_id, kind=kind, start=start, end=start+1,
                    spoken='дома', expected='сада', severity='high',
                    rechecked=True, dual_confirmed=True, dual_checked=True,
                    reason='Проверить по аудио')

    def test_rejected_issue_removed_from_corrections_but_kept_in_project(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'correct.docx'
            rows = [self.candidate('false', 100), self.candidate('todo', 200)]
            generate_report(path, dict(audio_name='a.mp3', text_name='b.docx',
                                       report_policy='dual-gigaam'),
                            rows, 0, 300, reviews={'false':'false', 'todo':'todo'})
            d = Document(path)
            content = '\n'.join(c.text for t in d.tables for row in t.rows for c in row.cells)
            self.assertNotIn('00:01:40', content)
            self.assertIn('00:03:20', content)
            self.assertEqual(len(rows), 2)  # Raw evidence is unchanged

    def test_exported_report_timecodes_are_extractable_but_not_auto_verified(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / 'report.docx'
            generate_report(path, dict(audio_name='a.mp3', text_name='b.docx',
                                       report_policy='dual-gigaam'),
                            [self.candidate('id', 6*3600 + 12*60 + 31)],
                            6*3600, 6*3600 + 1200)
            out = root/'findings.csv'
            self.assertEqual(benchmark.extract_docx(path, out), 1)
            with out.open(encoding='utf-8-sig', newline='') as fh:
                data = list(csv.DictReader(fh))
            self.assertEqual(data[0]['time'], '06:12:31')
            self.assertEqual(data[0]['importance'], 'review')

    def test_high_priority_ui_count_excludes_unconfirmed_and_rejected(self):
        bridge = Bridge()
        job = {'id':'a'*32, 'duration':100, 'parts':[],
               'report_policy':'dual-gigaam', 'audio_name':'file.mp3',
               'text_name':'source.txt'}
        missing = self.candidate('unconfirmed', 10)
        missing['dual_confirmed'] = False
        rejected = self.candidate('rejected', 20)
        confirmed = self.candidate('confirmed', 30)
        with patch('audiobook_qc.desktop_web.worker.load_job', return_value=job), \
             patch('audiobook_qc.desktop_web.worker.all_issues',return_value=[missing,rejected,confirmed]), \
             patch('audiobook_qc.desktop_web.worker.read_json',return_value={'rejected':'false'}):
            item = bridge.get_project('a'*32)
        self.assertEqual(item['high_count'], 1)
        self.assertEqual(len(item['issues']), 1)


class WorkflowValidationTests(unittest.TestCase):
    def test_github_workflow_has_no_duplicate_yaml_keys(self):
        try:
            import yaml
        except ImportError:
            self.skipTest('PyYAML is installed by the build requirements')
        class StrictLoader(yaml.SafeLoader):
            pass
        def strict_mapping(loader, node):
            result = {}
            for key, value in node.value:
                name = loader.construct_object(key)
                if name in result:
                    raise ValueError('Duplicate YAML key: '+str(name))
                result[name] = loader.construct_object(value)
            return result
        StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, strict_mapping)
        manifest = yaml.load((ROOT/'.github/workflows/build.yml').read_text(encoding='utf-8'),
                             Loader=StrictLoader)
        self.assertIn('build', manifest['jobs'])
        self.assertIn('release', manifest['jobs'])
        targets = [x['target'] for x in manifest['jobs']['build']['strategy']['matrix']['include']]
        self.assertEqual(targets, ['Windows-x64', 'Mac-Apple-Silicon'])

    def test_cyrillic_documentation_paths_exist(self):
        self.assertTrue((ROOT/'docs/Путь-к-версии-1.0.md').is_file())
        self.assertTrue((ROOT/'docs/Аудит-платформ.md').is_file())
        self.assertTrue((ROOT/'docs/Платформы-и-план.md').is_file())


if __name__ == '__main__':
    unittest.main()

class FinalReportDownloadTests(unittest.TestCase):
    def test_final_word_can_be_saved_only_after_completion(self):
        import types
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            output=root/'saved.docx'
            source=root/'reports'/'Полный_отчёт.docx'
            source.parent.mkdir(parents=True)
            source.write_bytes(b'fake-data-Word')
            bridge=Bridge()
            class Window:
                def create_file_dialog(self, *_args, **_kwargs):
                    return str(output)
            bridge.window=Window()
            job={'status':'completed', 'parts':[]}
            with patch('audiobook_qc.desktop_web.worker.load_job', return_value=job), \
                 patch('audiobook_qc.desktop_web.worker.job_dir', return_value=root), \
                 patch.dict(sys.modules, {'webview': type('W', (), {'SAVE_DIALOG': 2})()}):
                self.assertTrue(bridge.save_report('a'*32, 0))
                self.assertEqual(output.read_bytes(), b'fake-data-Word')
            job['status']='paused'
            with patch('audiobook_qc.desktop_web.worker.load_job', return_value=job), \
                 patch('audiobook_qc.desktop_web.worker.job_dir', return_value=root):
                with self.assertRaisesRegex(ValueError, 'после завершения'):
                    bridge.save_report('a'*32, 0)

class CheckpointSafetyTests(unittest.TestCase):
    def test_rejects_json_with_wrong_word_layout_or_invalid_time(self):
        from audiobook_qc.worker import valid_chunk_checkpoint
        good = {'start': 120, 'end':240,
                'words':[{'word':'осень','start':120.2,'end':120.8}],
                'acoustic':[]}
        self.assertTrue(valid_chunk_checkpoint(good,120,240))
        for bad in (
            {**good, 'words':[{'word':'без времени'}]},
            {**good, 'words':[{'word':'word','start':float('nan'),'end':120.8}]},
            {**good, 'words':[{'word':'word','start':100000,'end':100002}]},
            {**good, 'words':'not a list'},
            {**good, 'acoustic':'not a list'},
            {**good, 'start':float('nan')},
            {**good, 'end':241},
        ):
            with self.subTest(data=bad):
                self.assertFalse(valid_chunk_checkpoint(bad,120,240))

    def test_cache_accepts_empty_speech_for_silent_chunk(self):
        from audiobook_qc.worker import valid_chunk_checkpoint
        self.assertTrue(valid_chunk_checkpoint({'start':0.0,'end':120.0,'words':[]},0.0,120.0))
