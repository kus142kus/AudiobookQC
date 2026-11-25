"""Safely convert pinned GigaAM HF .bin weights on a *modern* PyTorch builder.

Mac Intel cannot use PyTorch >=2.6 wheels, so this script MUST be run on a
separate trusted Linux runner with patched PyTorch, not on the Intel Mac.
Never disable the Transformers/PyTorch vulnerability gate.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from get_offline_model import sha256_file


def convert(folder: Path) -> Path:
    import torch
    from safetensors.torch import load_file, save_file
    from packaging.version import Version

    if Version(torch.__version__.split('+')[0]) < Version('2.6'):
        raise RuntimeError('Для безопасного преобразования требуется PyTorch >= 2.6')
    manifest_path = folder / 'model_manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    from audiobook_qc.dual_engine import check_model_hash, HF_REVISION
    if manifest.get('revision') != HF_REVISION:
        raise ValueError('Неподдерживаемая ревизия GigaAM')
    if 'pytorch_model.bin' not in manifest.get('files', {}):
        raise ValueError('Ожидаются проверенные исходные .bin веса GigaAM')
    check_model_hash(folder)
    source = folder / 'pytorch_model.bin'
    # The publisher's pinned HF checkpoint is a plain state_dict. Never
    # deserialize arbitrary objects, even from a repository with a fixed rev.
    state = torch.load(str(source), map_location='cpu', weights_only=True)
    if not isinstance(state, dict) or not state:
        raise ValueError('GigaAM checkpoint не является словарём весов')
    if not all(isinstance(k, str) and k and isinstance(v, torch.Tensor)
               for k, v in state.items()):
        raise ValueError('GigaAM checkpoint содержит не только тензоры')
    # Clone makes even tied/aliased weights writable in safe format.
    clean = {k: v.detach().cpu().contiguous().clone() for k, v in state.items()}
    del state
    target = folder / 'model.safetensors'
    temp = folder / 'model.safetensors.pending'
    try:
        save_file(clean, str(temp))
        read_back = load_file(str(temp), device='cpu')
        if read_back.keys() != clean.keys() or not all(
                torch.equal(read_back[k], clean[k]) for k in clean):
            raise ValueError('Преобразованные веса отличаются от исходных')
        del clean, read_back
        os.replace(temp, target)
        manifest['files'].pop('pytorch_model.bin')
        manifest['files']['model.safetensors'] = {
            'size': target.stat().st_size, 'sha256': sha256_file(target)}
        manifest['safe_conversion'] = {
            'source_sha256': sha256_file(source),
            'format': 'safetensors', 'weights_only': True,
            'torch_builder': torch.__version__,
        }
        temp_manifest = manifest_path.with_suffix('.json.pending')
        temp_manifest.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        os.replace(temp_manifest, manifest_path)
        source.unlink()
        check_model_hash(folder)
    finally:
        temp.unlink(missing_ok=True)
    print('AQC_GIGAAM_SAFETENSORS_CONVERSION_OK', target.stat().st_size, flush=True)
    return target


if __name__ == '__main__':
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
    convert(Path('models/gigaam'))
