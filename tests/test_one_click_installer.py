"""Protect the actual public installation flow from the old two-ZIP design."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('build_installer', ROOT/'scripts/build_installer.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class OneClickInstallerTests(unittest.TestCase):
    def test_staging_inside_app_is_refused_without_deleting_anything(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            app=root/'the-app'
            app.mkdir()
            original=app/'AudiobookQC.exe'
            original.write_bytes(b'MZimportant')
            with self.assertRaisesRegex(ValueError, 'overlaps application'):
                builder.prepare(app, app/'staging', root/'model.zip')
            self.assertEqual(original.read_bytes(), b'MZimportant')

    def test_cannot_delete_app_by_passing_parent_staging(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            app=root/'app'
            app.mkdir()
            (app/'AudiobookQC.exe').write_bytes(b'MZimportant')
            with self.assertRaisesRegex(ValueError, 'overlaps application'):
                builder.prepare(app, root, root/'model.zip')
            self.assertTrue((app/'AudiobookQC.exe').is_file())

    def test_synthetic_bundle_roundtrip_and_inno_template(self):
        builder.source_self_test()

    def test_model_archive_only_has_expected_internal_paths(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            app = base/'AudiobookQC'
            data = {
                'AudiobookQC.exe': b'MZdummy',
                '_internal/models/medium/model.bin': b'w'*100_000_001,
                '_internal/models/medium/config.json': b'{}',
                '_internal/models/medium/tokenizer.json': b'{}',
                '_internal/models/gigaam/pytorch_model.bin': b'gigaam',
                '_internal/models/events/yamnet.onnx': b'yamnet',
            }
            for name, blob in data.items():
                p=app/name; p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(blob)
            m=builder.prepare(app,base/'core',base/'AQC-Model.zip')
            with ZipFile(base/'AQC-Model.zip') as z:
                self.assertEqual(set(z.namelist()),{
                    '_internal/models/medium/model.bin',
                    '_internal/models/medium/config.json',
                    '_internal/models/medium/tokenizer.json',
                })
            self.assertEqual(m['model_unpacked_bytes'],
                             sum(len(v) for k,v in data.items() if k.startswith('_internal/models/medium/')))
            self.assertLess(m['model_bytes'], m['model_unpacked_bytes'])
            self.assertEqual(len(m['model_sha256']),64)
            self.assertEqual(len(m['model_bin_sha256']),64)

    def test_real_public_installer_downloads_verified_model(self):
        s=(ROOT/'installer/AudiobookQC.iss.in').read_text(encoding='utf-8')
        for token in ('Hash:', 'ExternalSize:', 'download extractarchive',
                      'GetSHA256OfFile(', '--self-test', 'PrivilegesRequired=lowest'):
            if token == 'download extractarchive':
                self.assertIn('@@MODEL_FLAGS@@',s)
            else:
                self.assertIn(token,s)
        self.assertIn('@@MODEL_SHA@@',s)
        self.assertIn('@@MODEL_BIN_SHA@@',s)

    def test_windows_public_setup_single_download_and_explicit_publish(self):
        s=(ROOT/'.github/workflows/build.yml').read_text(encoding='utf-8')
        self.assertIn('build_installer.py --test-inno',s)
        self.assertLess(s.index('build_installer.py --test-inno'),s.index('Download and checksum BOTH models'))
        self.assertIn('build_installer.py --prepare',s)
        self.assertIn('build_installer.py --compile',s)
        self.assertIn('release/AQC-Setup.exe',s)
        self.assertIn('release/AQC-Model.zip',s)
        self.assertIn('publish_beta',s)
        self.assertIn('needs: build',s)
        self.assertIn('prerelease: true',s)


if __name__=='__main__': unittest.main()
