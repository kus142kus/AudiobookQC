"""Bundle two offline speech models on the GitHub build runner.

The publisher's GigaAM file also contains optional *longform* diarization via
pyannote. Transformers eagerly checks every static import in remote-code files,
including imports in functions we never call for our <=23-second crops. In the
pinned upstream file those imports are changed to lazy imports so shortform ASR
has no unnecessary pyannote/torchcodec/gated-model dependencies.

The original file checksum, patched file checksum and pinned revision are all
recorded in the local manifest. No TLS verification is disabled.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

WHISPER = 'Systran/faster-whisper-medium'
GIGAAM = 'ai-sage/GigaAM-v3'
GIGA_REVISION = 'cec030b4c4f35d928e4a9044a3bdb29ebd499fac'


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for piece in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(piece)
    return digest.hexdigest()


def defer_optional_longform_pyannote_imports(source: str) -> tuple[str, int]:
    """Defer only pyannote's longform VAD imports; shortform stays unchanged.

    If publisher changes import layout, refuse to build instead of silently
    shipping broken or remotely dependent inference code.
    """
    replacements = {
        'from pyannote.audio import Model':
            'Model = __import__("pyannote.audio", fromlist=["Model"]).Model',
        'from pyannote.audio.pipelines import VoiceActivityDetection':
            'VoiceActivityDetection = __import__("pyannote.audio.pipelines", fromlist=["VoiceActivityDetection"]).VoiceActivityDetection',
        'from pyannote.audio import Model, Pipeline':
            '_pyannote = __import__("pyannote.audio", fromlist=["Model", "Pipeline"]); Model, Pipeline = _pyannote.Model, _pyannote.Pipeline',
        'from pyannote.audio import Model, Pipeline, Audio':
            '_pyannote = __import__("pyannote.audio", fromlist=["Model", "Pipeline", "Audio"]); Model, Pipeline, Audio = _pyannote.Model, _pyannote.Pipeline, _pyannote.Audio',
    }
    rewritten = 0
    updated_lines = []
    for line in source.splitlines(keepends=True):
        match = re.fullmatch(r'([ \t]*)(from\s+pyannote\.audio(?:\.pipelines)?\s+import\s+[^\r\n#]+)(\s*(?:#.*)?)(\r?\n?)', line)
        if match:
            indentation, statement, suffix, eol = match.groups()
            statement = statement.strip()
            if statement not in replacements:
                raise RuntimeError(f'Unexpected pyannote import in pinned GigaAM source: {statement}')
            updated_lines.append(indentation + replacements[statement] + suffix + eol)
            rewritten += 1
        else:
            updated_lines.append(line)
    result = ''.join(updated_lines)
    # Prevent a future upstream change from reintroducing a hard dependency.
    if re.search(r'(?m)^\s*(?:from\s+pyannote\b|import\s+pyannote\b)', result):
        raise RuntimeError('Static pyannote import remains in pinned GigaAM source')
    if rewritten == 0:
        raise RuntimeError('GigaAM upstream code no longer matches the known pyannote optional-import layout; re-audit required')
    compile(result, 'modeling_gigaam.py', 'exec')
    return result, rewritten


def make_shortform_compatible(source_file: Path) -> dict:
    original_sha = sha256_file(source_file)
    source = source_file.read_text(encoding='utf-8')
    modified, count = defer_optional_longform_pyannote_imports(source)
    source_file.write_text(modified, encoding='utf-8')
    return {'original_sha256': original_sha,
            'modified_sha256': sha256_file(source_file),
            'deferred_longform_pyannote_imports': count,
            'note': 'Only the optional longform pyannote imports were deferred; 23s shortform inference unchanged'}


def download_whisper() -> None:
    from huggingface_hub import snapshot_download

    whisper = Path('models/medium')
    whisper.mkdir(parents=True, exist_ok=True)
    snapshot_download(repo_id=WHISPER, local_dir=str(whisper), allow_patterns=[
        'model.bin', 'config.json', 'tokenizer.json',
        'preprocessor_config.json', 'vocabulary.*', 'LICENSE', 'README.md'
    ])
    for name in ('model.bin', 'config.json', 'tokenizer.json'):
        path = whisper / name
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f'Whisper model missing: {name}')
    if (whisper / 'model.bin').stat().st_size < 100_000_000:
        raise RuntimeError('Whisper model weights unexpectedly small')
    print('Whisper medium downloaded and checked', flush=True)


def download_gigaam() -> None:
    from huggingface_hub import snapshot_download

    giga = Path('models/gigaam')
    giga.mkdir(parents=True, exist_ok=True)
    wanted = ['config.json', 'modeling_gigaam.py',
              'pytorch_model.bin', 'tokenizer.model']
    snapshot_download(repo_id=GIGAAM, revision=GIGA_REVISION,
                      local_dir=str(giga), allow_patterns=wanted, max_workers=4)
    for name in wanted:
        path = giga / name
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f'GigaAM pinned model incomplete: {name}')
    if (giga / 'pytorch_model.bin').stat().st_size < 100_000_000:
        raise RuntimeError('GigaAM model weights unexpectedly small')

    patch = make_shortform_compatible(giga / 'modeling_gigaam.py')
    manifest = {
        'source': GIGAAM, 'revision': GIGA_REVISION,
        'shortform_compat': patch,
        'files': {name: {'size': (giga / name).stat().st_size,
                         'sha256': sha256_file(giga / name)} for name in wanted},
    }
    (giga / 'model_manifest.json').write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8')
    print('Offline GigaAM e2e CTC downloaded; longform-only pyannote imports deferred:',
          patch['deferred_longform_pyannote_imports'], flush=True)
    print('Model revision:', GIGA_REVISION, flush=True)



def download_models() -> None:
    download_whisper()
    download_gigaam()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--gigaam-only', action='store_true',
                      help='Only download GigaAM (secure Linux checkpoint conversion)')
    mode.add_argument('--whisper-only', action='store_true',
                      help='Only download Whisper (Intel runner receives verified safe GigaAM)')
    options = parser.parse_args()
    if options.gigaam_only:
        download_gigaam()
    elif options.whisper_only:
        download_whisper()
    else:
        download_models()
