"""Guard against platform-preview failures observed in GitHub Actions."""
import hashlib
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))
from audiobook_qc.dual_engine import check_model_hash, load_secondary_model


class PlatformPreviewRegressions(unittest.TestCase):
    def test_windows_arm_preview_accepts_inno_x86_stub(self):
        wf = (ROOT/'.github/workflows/platform-matrix-preview.yml').read_text()
        self.assertIn('0x014c, 0x8664', wf)
        self.assertIn('not proof of payload architecture', wf)
        self.assertNotIn('if ($machine -ne 0x8664)', wf)

    def test_linux_jobs_install_portaudio_before_source_tests(self):
        for name in ('platform-matrix-preview.yml','linux-preview.yml','linux-arm64-preview.yml'):
            with self.subTest(name=name):
                text = (ROOT/'.github/workflows'/name).read_text()
                self.assertIn('libportaudio2', text)
                self.assertLess(text.find('libportaudio2'),text.find('python -m unittest'))

    def test_clip_player_stop_without_portaudio_does_not_crash(self):
        from audiobook_qc.desktop import ClipPlayer
        player = ClipPlayer()
        with patch.dict(sys.modules, {'sounddevice':None}):
            player.stop()  # no import available
        with patch.dict(sys.modules, {'sounddevice':MagicMock()}):
            player.stop()
        # Simulate dependency raising an OSError during module import.
        import builtins
        original = builtins.__import__
        def broken_import(name, *args, **kwargs):
            if name == 'sounddevice':
                raise OSError('PortAudio library not found')
            return original(name,*args,**kwargs)
        with patch('builtins.__import__', side_effect=broken_import):
            player.stop()

    def test_intel_preview_uses_safe_artifact_not_vulnerable_bin_loading(self):
        wf=(ROOT/'.github/workflows/platform-matrix-preview.yml').read_text()
        self.assertIn('intel-safe-gigaam:',wf)
        self.assertIn('convert_gigaam_safetensors.py',wf)
        self.assertIn('name: AQC-Intel-GigaAM-SAFE',wf)
        self.assertIn('python scripts/get_offline_model.py --whisper-only',wf)
        self.assertNotIn('check_torch_load_is_safe =',wf)
        self.assertNotIn('weights_only=False',wf)

    def make_bundle(self, folder, safe=False):
        folder.mkdir()
        files=('config.json','modeling_gigaam.py','tokenizer.model',
               'model.safetensors' if safe else 'pytorch_model.bin')
        manifest_files={}
        for name in files:
            data=name.encode(); (folder/name).write_bytes(data)
            manifest_files[name]={'size':len(data),'sha256':hashlib.sha256(data).hexdigest()}
        from audiobook_qc.dual_engine import HF_REVISION
        (folder/'model_manifest.json').write_text(json.dumps({'revision':HF_REVISION,'files':manifest_files}))

    def test_safetensors_variant_manifest_requires_exact_hash(self):
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/'gigaam'; self.make_bundle(f,safe=True)
            check_model_hash(f)
            (f/'model.safetensors').write_bytes(b'changed')
            with self.assertRaises(ValueError): check_model_hash(f)

    def test_safetensors_selected_explicitly(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)/'gigaam'; self.make_bundle(folder,safe=True)
            fake=MagicMock(); fake.model=MagicMock()
            fake_lib=MagicMock(); fake_lib.AutoModel.from_pretrained.return_value=fake
            with patch('audiobook_qc.dual_engine.resolve_secondary_dir',return_value=folder),\
                patch.dict(sys.modules, {'transformers':fake_lib}):
                load_secondary_model()
            self.assertTrue(fake_lib.AutoModel.from_pretrained.call_args.kwargs['use_safetensors'])

    def test_safe_conversion_real_tensor_roundtrip(self):
        try:
            import torch
            from packaging.version import Version
            import safetensors
        except ImportError:
            self.skipTest('PyTorch / safetensors unavailable in this test environment')
        if Version(torch.__version__.split('+')[0]) < Version('2.6'):
            self.skipTest('Conversion deliberately runs only with patched PyTorch >=2.6')
        from audiobook_qc.dual_engine import HF_REVISION
        from convert_gigaam_safetensors import convert
        with tempfile.TemporaryDirectory() as td:
            directory=Path(td)/'gigaam'
            directory.mkdir()
            entries={}
            for name in ('config.json','modeling_gigaam.py','tokenizer.model'):
                payload=name.encode()
                (directory/name).write_bytes(payload)
                entries[name]={'size':len(payload),'sha256':hashlib.sha256(payload).hexdigest()}
            weights=directory/'pytorch_model.bin'
            torch.save({'layer.weight':torch.arange(6).reshape(2,3).float()},weights)
            entries[weights.name]={'size':weights.stat().st_size,'sha256':hashlib.sha256(weights.read_bytes()).hexdigest()}
            (directory/'model_manifest.json').write_text(json.dumps({'revision':HF_REVISION,'files':entries}))
            result=convert(directory)
            check_model_hash(directory)
            self.assertTrue(result.is_file())
            self.assertFalse(weights.exists())
            self.assertTrue(torch.equal(safetensors.torch.load_file(str(result))['layer.weight'], torch.arange(6).reshape(2,3).float()))

    def test_converter_never_allows_pickle_objects(self):
        script=(ROOT/'scripts/convert_gigaam_safetensors.py').read_text()
        self.assertIn('weights_only=True',script)
        self.assertNotIn('weights_only=False',script)
        self.assertIn("Version('2.6')",script)


if __name__ == '__main__': unittest.main()
