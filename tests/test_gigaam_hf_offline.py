"""Regression tests for publisher Hugging Face GigaAM offline bundles."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from audiobook_qc.dual_engine import check_model_hash, load_secondary_model, HF_REVISION

class PublisherHfModelTests(unittest.TestCase):
    def make_fake_bundle(self, root):
        folder = root/'gigaam'
        folder.mkdir()
        entries = {}
        for name in ('config.json', 'modeling_gigaam.py', 'pytorch_model.bin', 'tokenizer.model'):
            value = (name+'а').encode('utf-8')
            (folder/name).write_bytes(value)
            entries[name] = {'size':len(value), 'sha256':hashlib.sha256(value).hexdigest()}
        (folder/'model_manifest.json').write_text(json.dumps({'revision':HF_REVISION,'files':entries}), encoding='utf8')
        return folder

    def test_manifest_checks_corrupt_files(self):
        with tempfile.TemporaryDirectory() as td:
            folder=self.make_fake_bundle(Path(td))
            check_model_hash(folder)
            (folder/'tokenizer.model').write_bytes(b'garbled')
            with self.assertRaises(ValueError): check_model_hash(folder)

    def test_missing_manifest_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            folder=self.make_fake_bundle(Path(td))
            (folder/'model_manifest.json').unlink()
            with self.assertRaises(Exception): check_model_hash(folder)

    def test_hf_backend_called_without_network_or_external_ffmpeg(self):
        with tempfile.TemporaryDirectory() as td:
            folder=self.make_fake_bundle(Path(td))
            fake_inner=MagicMock()
            fake_model=MagicMock()
            fake_model.cpu.return_value=fake_model
            fake_model.float.return_value=fake_model
            fake_model.eval.return_value=fake_model
            fake_model.model=fake_inner
            fake_transformers=MagicMock()
            fake_transformers.AutoModel.from_pretrained.return_value=fake_model
            with patch('audiobook_qc.dual_engine.resolve_secondary_dir',return_value=folder), \
                 patch.dict(sys.modules, {'transformers':fake_transformers}):
                loaded=load_secondary_model()
            self.assertIs(loaded, fake_model)
            args, kwargs=fake_transformers.AutoModel.from_pretrained.call_args
            self.assertEqual(args[0],str(folder))
            self.assertTrue(kwargs['local_files_only'])
            self.assertTrue(kwargs['trust_remote_code'])
            self.assertTrue(hasattr(fake_inner.prepare_wav,'__call__'))

if __name__=='__main__':unittest.main()
