"""Test the stdlib static checker itself to prevent silent coverage regressions."""
from pathlib import Path
import tempfile
import unittest
from scripts.static_undefined_names import check_paths, DEFAULT_PATHS


class NameAuditTests(unittest.TestCase):
    def test_current_project_has_no_undefined_globals(self):
        self.assertEqual(check_paths(DEFAULT_PATHS), [])

    def test_missing_runtime_import_is_caught(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / 'bad.py'
            source.write_text('def test_events():\n    return wave.open("test.wav")\n', encoding='utf-8')
            issues = check_paths([source])
            self.assertEqual(len(issues), 1)
            self.assertIn("undefined global 'wave'", issues[0])

    def test_local_import_is_valid(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / 'ok.py'
            source.write_text('def test_events():\n    import wave\n    return wave.open("test.wav")\n', encoding='utf-8')
            self.assertEqual(check_paths([source]), [])


if __name__ == '__main__':
    unittest.main()
