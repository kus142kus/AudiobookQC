"""Regression guard for real Mac error: missing python-docx template XML."""
from __future__ import annotations
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from run import _verify_word_export

class FrozenDocxTests(unittest.TestCase):
    def test_all_frozen_targets_collect_docx_templates(self):
        cases = {
            '.github/workflows/build.yml': 1,
            '.github/workflows/platform-matrix-preview.yml': 2,
            '.github/workflows/linux-preview.yml': 1,
            '.github/workflows/linux-arm64-preview.yml': 1,
        }
        for relative, expected in cases.items():
            with self.subTest(relative=relative):
                text=(ROOT / relative).read_text(encoding='utf-8')
                self.assertEqual(text.count('--collect-data docx'), expected)
                self.assertEqual(text.count('python -m PyInstaller'), expected)

    def test_real_word_report_including_header_and_footer(self):
        with patch('run._stage') as stage:
            _verify_word_export()
            stage.assert_any_call('AUDIOBOOK_QC_WORD_REPORT_EXPORT_OK')

    def test_frozen_self_test_exercises_real_word_report(self):
        text=(ROOT/'run.py').read_text(encoding='utf-8')
        self.assertIn('def _verify_word_export()', text)
        self.assertIn('    _verify_word_export()\n    _stage("AUDIOBOOK_QC_OFFLINE_BUNDLE_OK")',text)
        check=(ROOT/'scripts/check_bundle.py').read_text(encoding='utf-8')
        self.assertIn('("--self-test", 180)',check)

if __name__ == '__main__':
    unittest.main()
