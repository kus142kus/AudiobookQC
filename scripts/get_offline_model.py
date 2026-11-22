"""Download both OFFLINE inference models on the GitHub build runner only.

The GigaAM v3 e2e CTC revision comes from its publisher's Hugging Face
repository. It replaces the upstream CDN with its expired TLS certificate.
All transfers use verified HTTPS. No model is downloaded by end-user app.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from huggingface_hub import snapshot_download

WHISPER = 'Systran/faster-whisper-medium'
GIGAAM = 'ai-sage/GigaAM-v3'
# Pinned publisher revision, variant e2e_ctc (not the default RNNT model).
GIGA_REVISION = 'cec030b4c4f35d928e4a9044a3bdb29ebd499fac'


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(part)
    return digest.hexdigest()


def download_models() -> None:
    whisper = Path('models/medium')
    whisper.mkdir(parents=True, exist_ok=True)
    snapshot_download(repo_id=WHISPER, local_dir=str(whisper), allow_patterns=[
        'model.bin', 'config.json', 'tokenizer.json',
        'preprocessor_config.json', 'vocabulary.*', 'LICENSE', 'README.md'
    ])
    for name in ('model.bin', 'config.json', 'tokenizer.json'):
        path = whisper/name
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f'Whisper model missing: {name}')
    if (whisper/'model.bin').stat().st_size < 100_000_000:
        raise RuntimeError('Whisper model weights unexpectedly small')
    print('Whisper medium downloaded and checked', flush=True)

    giga = Path('models/gigaam')
    giga.mkdir(parents=True, exist_ok=True)
    wanted = ['config.json', 'modeling_gigaam.py',
              'pytorch_model.bin', 'tokenizer.model']
    snapshot_download(repo_id=GIGAAM, revision=GIGA_REVISION,
                      local_dir=str(giga), allow_patterns=wanted,
                      max_workers=4)
    for name in wanted:
        path = giga/name
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f'GigaAM publisher model incomplete: {name}')
    if (giga/'pytorch_model.bin').stat().st_size < 100_000_000:
        raise RuntimeError('GigaAM model weights unexpectedly small')
    # Pinned revision + TLS + manifest prevents accepting truncated local data.
    manifest = {'source': GIGAAM, 'revision': GIGA_REVISION,
                'files': {name: {'size': (giga/name).stat().st_size,
                                 'sha256': sha256_file(giga/name)} for name in wanted}}
    (giga/'model_manifest.json').write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8')
    print('Offline GigaAM v3 e2e CTC downloaded from publisher HF:',
          manifest['files']['pytorch_model.bin']['size'], 'bytes', flush=True)


if __name__ == '__main__':
    download_models()
