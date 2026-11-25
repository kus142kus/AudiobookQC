"""Avoid shipping substituted artifacts or broken model download tags."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT/'scripts'/f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checker = load('check_release_assets')
contract = load('verify_release_contract')


class ReleaseIntegrityTests(unittest.TestCase):
    def test_tag_matches_real_release_workflow(self):
        self.assertEqual(contract.check(), 'v1.0.0-rc.1')

    def test_tamper_detected_without_overwriting_sidecar(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'AQC-Setup.exe'
            path.write_bytes(b'MZgood-original')
            self.assertEqual(checker.main([str(path)]), 0)
            sidecar = path.with_name(path.name+'.sha256')
            original = sidecar.read_bytes()
            path.write_bytes(b'MZtampered-setup')
            with self.assertRaisesRegex(ValueError,'changed since build'):
                checker.main(['--verify', str(path)])
            self.assertEqual(sidecar.read_bytes(), original)

    def test_good_checksum_is_verified_read_only(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'AQC-Setup.exe'
            path.write_bytes(b'MZtest-file')
            checker.main([str(path)])
            self.assertEqual(checker.main(['--verify',str(path)]), 0)

    def test_missing_checksum_blocks_release(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'AQC-Setup.exe'
            path.write_bytes(b'MZtest-file')
            with self.assertRaises(FileNotFoundError):
                checker.main(['--verify',str(path)])

    def test_zip_traversal_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'AQC-Model.zip'
            with ZipFile(path,'w') as z:
                z.writestr('_internal/models/medium/model.bin',b'model')
                z.writestr('_internal/models/medium/../../bad',b'bad')
            with self.assertRaisesRegex(ValueError,'unsafe archive'):
                checker.inspect_asset(path)

    def test_duplicate_model_entries_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'AQC-Model.zip'
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', UserWarning)
                with ZipFile(path,'w') as z:
                    z.writestr('_internal/models/medium/model.bin',b'model')
                    z.writestr('_internal/models/medium/model.bin',b'model2')
            with self.assertRaisesRegex(ValueError,'duplicate archive'):
                checker.inspect_asset(path)

    def test_ci_release_step_is_verify_not_recompute(self):
        data = (ROOT/'.github/workflows/build.yml').read_text(encoding='utf-8')
        block = data.split('name: Verify build-produced checksums WITHOUT rewriting them')[1]
        self.assertIn('check_release_assets.py --verify',block)
        self.assertNotIn('sha256sum -c ',block)


if __name__ == '__main__':
    unittest.main()
