"""Build-contract tests: optional pyannote does not block shortform ASR."""
import ast
import importlib.util
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('aqc_model_download', ROOT / 'scripts/get_offline_model.py')
model_download = importlib.util.module_from_spec(spec)
spec.loader.exec_module(model_download)


class OptionalModelImportTests(unittest.TestCase):
    def test_longform_optional_dependencies_deferred(self):
        original = '''\ndef get_pipeline(device):\n    from pyannote.audio import Model\n    from pyannote.audio.pipelines import VoiceActivityDetection\n    return Model, VoiceActivityDetection\n'''
        changed, count = model_download.defer_optional_longform_pyannote_imports(original)
        self.assertEqual(count, 2)
        self.assertNotIn('from pyannote.audio import', changed)
        self.assertNotIn('from pyannote.audio.pipelines import', changed)
        # Importing or compiling the GigaAM file must not import pyannote.
        ast.parse(changed)
        namespace = {}
        exec(changed, namespace)
        self.assertTrue(callable(namespace['get_pipeline']))
        self.assertIn('pyannote', changed)  # Still available lazily for longform.

    def test_unknown_import_is_not_silently_ignored(self):
        with self.assertRaisesRegex(RuntimeError, 'Unexpected pyannote import'):
            model_download.defer_optional_longform_pyannote_imports(
                'def f():\n    from pyannote.audio import Unknown\n')

    def test_old_style_import_layout_must_be_present(self):
        with self.assertRaisesRegex(RuntimeError, 're-audit'):
            model_download.defer_optional_longform_pyannote_imports('def f():\n    return 1\n')

    def test_publisher_dependency_versions_are_fixed(self):
        deps = (ROOT / 'requirements-build.txt').read_text(encoding='utf-8')
        self.assertIn('transformers==4.57.1', deps)
        self.assertIn('torch==2.8.0', deps)
        self.assertIn('torchaudio==2.8.0', deps)
        # pyannote and torchcodec are only needed for transcribe_longform.
        self.assertNotRegex(deps, r'(?m)^pyannote')

    def test_no_network_fallback_or_insecure_ssl(self):
        code = (ROOT / 'scripts/get_offline_model.py').read_text(encoding='utf-8')
        self.assertNotIn('_create_unverified_context', code)
        self.assertNotIn('verify=False', code)
        self.assertIn('revision=GIGA_REVISION', code)

if __name__ == '__main__':
    unittest.main()

class BundleManifestIntegrationTests(unittest.TestCase):
    def test_offline_manifest_hash_survives_local_patch(self):
        import json
        import tempfile
        import hashlib
        with tempfile.TemporaryDirectory() as tempdir:
            folder = Path(tempdir)
            names = ('config.json','modeling_gigaam.py','pytorch_model.bin','tokenizer.model')
            for name in names:
                (folder/name).write_bytes(b'fixture data')
            (folder/'modeling_gigaam.py').write_text(
                'def g():\n    from pyannote.audio import Model\n    return Model\n',
                encoding='utf-8')
            meta = model_download.make_shortform_compatible(folder/'modeling_gigaam.py')
            self.assertNotEqual(meta['original_sha256'],meta['modified_sha256'])
            manifest = {
                'revision': model_download.GIGA_REVISION,
                'shortform_compat': meta,
                'files': {n: {'size': (folder/n).stat().st_size,
                              'sha256': model_download.sha256_file(folder/n)} for n in names},
            }
            (folder/'model_manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
            sys.path.insert(0, str(ROOT / 'src'))
            from audiobook_qc.dual_engine import check_model_hash
            check_model_hash(folder)
            (folder/'modeling_gigaam.py').write_text('tampered!', encoding='utf-8')
            with self.assertRaises(ValueError):
                check_model_hash(folder)
