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
            'tag': 'v1.0.0-rc.2',
            'url': 'https://github.com/kus142kus/AudiobookQC/releases/download/v1.0.0-rc.2/AQC-Model.zip',
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
            self.assertIn('Flags: external extractarchive recursesubdirs createallsubdirs ignoreversion', model_entry)
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
            self.assertIn('Flags: external download extractarchive recursesubdirs createallsubdirs ignoreversion', entry)
            self.assertIn(self._manifest()['url'], entry)
            self.assertIn('OutputBaseFilename=AQC-Setup', script)

    def test_inno_template_logs_actual_install_destination(self):
        template = (ROOT / 'installer/AudiobookQC.iss.in').read_text(encoding='utf-8')
        self.assertIn("Log('AQC installer actual destination: '", template)

    def test_installer_verifies_extracted_model_hash_after_install(self):
        template = (ROOT / 'installer/AudiobookQC.iss.in').read_text(encoding='utf-8')
        self.assertIn('Whisper model SHA-256 verification failed after installation', template)
        self.assertIn('GetSHA256OfFile(ExpandConstant(', template)
        self.assertIn("'@@MODEL_BIN_SHA@@') <> 0", template)

    def test_ci_failure_reports_missing_file_log_and_tree(self):
        with tempfile.TemporaryDirectory() as t:
            folder = Path(t)
            installation = folder / 'installation'
            installation.mkdir()
            (installation/'AudiobookQC.exe').write_bytes(b'MZok')
            (folder/'setup.log').write_text('Destination directory: wrong place', encoding='utf-8')
            with self.assertRaises(RuntimeError) as failure:
                builder._verify_inno_payload(
                    {'AudiobookQC.exe': b'MZok', '_internal/models/medium/model.bin': b'123'},
                    installation, folder/'setup.log', folder)
            report = str(failure.exception)
            self.assertIn('model.bin', report)
            self.assertIn('Destination directory: wrong place', report)
            self.assertIn('AudiobookQC.exe', report)
            self.assertIn('Expected installation directory:', report)

    def test_ci_payload_checker_detects_damaged_file(self):
        with tempfile.TemporaryDirectory() as t:
            folder = Path(t)
            installation = folder/'installation'
            installation.mkdir()
            (installation/'AudiobookQC.exe').write_bytes(b'bad')
            with self.assertRaisesRegex(RuntimeError, 'file content is wrong'):
                builder._verify_inno_payload(
                    {'AudiobookQC.exe': b'MZok'}, installation, folder/'setup.log', folder)

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
