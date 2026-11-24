import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
from check_release_assets import inspect_asset, MAX_BYTES
from macos_bundle_audit import audit

class DistributionTests(unittest.TestCase):
    def test_small_win_package_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'Windows.zip'
            with zipfile.ZipFile(p, 'w') as f:
                f.writestr('AudiobookQC/AudiobookQC.exe', b'MZfake')
            self.assertEqual(len(inspect_asset(p).split()[0]),64)

    def test_zip_without_exe_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'Windows.zip'
            with zipfile.ZipFile(p,'w') as f: f.writestr('hello.txt','text')
            with self.assertRaisesRegex(ValueError,'EXE'):
                inspect_asset(p)

    def test_release_2gib_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'huge.dmg'
            with open(p,'wb') as f:
                f.truncate(MAX_BYTES)
            with self.assertRaisesRegex(ValueError,'2 GiB'):
                inspect_asset(p)

    def test_macos_requires_info_plist(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'Test.app'
            p.mkdir()
            with self.assertRaises(FileNotFoundError):
                audit(p)

if __name__ == '__main__': unittest.main()
