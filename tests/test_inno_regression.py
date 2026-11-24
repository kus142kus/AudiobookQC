"""Regressions for the Windows one-click installer CI tests (2026-11-24)."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('aqc_builder_inno_regression', ROOT / 'scripts/build_installer.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class InstallerInnoRegressions(unittest.TestCase):
    def _manifest(self):
        return {
            'tag': 'v0.9.1-beta.1',
            'url': 'https://github.com/kus142kus/AudiobookQC/releases/download/v0.9.1-beta.1/AQC-Model.zip',
            'model_unpacked_bytes': 120_000_000,
            'model_sha256': 'a' * 64,
            'model_bin_sha256': 'b' * 64,
        }

    def test_local_fixture_omits_destname_and_download_but_preserves_hash(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            source = str(root / 'AQC-Model.zip')
            file = builder.render(self._manifest(), staged=root/'core', filename=root/'test.iss',
                                  source_override=source, out_dir=root, test=True)
            script = file.read_text(encoding='utf-8-sig')
            model_entry = next(line for line in script.splitlines() if 'Hash:' in line)
            self.assertNotIn('DestName:', model_entry)
            self.assertIn('Flags: external extractarchive ignoreversion', model_entry)
            self.assertNotIn(' download ', model_entry)
            self.assertIn('Hash: "' + 'a' * 64 + '"', model_entry)
            self.assertIn('OutputBaseFilename=AQC-CI-Test', script)

    def test_public_installer_keeps_required_download_destname(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            file = builder.render(self._manifest(), staged=root/'core', filename=root/'release.iss', out_dir=root)
            script = file.read_text(encoding='utf-8-sig')
            entry = next(line for line in script.splitlines() if 'Hash:' in line)
            self.assertIn('DestName: "AQC-Model.zip"', entry)
            self.assertIn('Flags: external download extractarchive ignoreversion', entry)
            self.assertIn(self._manifest()['url'], entry)
            self.assertIn('OutputBaseFilename=AQC-Setup', script)

    def test_compile_uses_explicit_test_output_dir_not_production_dist(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            script = root / 'test.iss'
            script.write_text('OutputBaseFilename=AQC-CI-Test\n', encoding='utf-8')
            compiled = root / 'AQC-CI-Test.exe'
            compiled.write_bytes(b'MZfake')
            with patch.object(builder.subprocess, 'run') as run:
                output = builder.compile_iss(script, compiler=root/'iscc.exe',
                                             output_dir=root, output_name='AQC-CI-Test')
            self.assertEqual(output, compiled)
            run.assert_called_once()

    def test_compile_fails_if_installer_not_in_requested_directory(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            script = root / 'test.iss'
            script.write_text('OutputBaseFilename=AQC-CI-Test\n', encoding='utf-8')
            with patch.object(builder.subprocess, 'run'):
                with self.assertRaises(FileNotFoundError):
                    builder.compile_iss(script, compiler=root/'iscc.exe',
                                        output_dir=root, output_name='AQC-CI-Test')


if __name__ == '__main__':
    unittest.main()
