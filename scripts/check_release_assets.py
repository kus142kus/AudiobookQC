"""Reject corrupt/oversize public release assets and emit SHA-256 sidecars.

Every file uploaded to GitHub Releases must be <2 GiB.  The Windows installer
(AQC-Setup.exe) auto-installs its matching Whisper model (AQC-Model.zip).
Only AQC-Setup.exe is downloaded manually by the Windows user.
"""
from __future__ import annotations
import hashlib
from pathlib import Path
import sys
from zipfile import ZipFile

MAX_BYTES = 2 * 1024**3


def inspect_asset(path: Path) -> str:
    path = Path(path)
    if not path.is_file() or path.stat().st_size == 0:
        raise FileNotFoundError(f'Missing release asset: {path}')
    size = path.stat().st_size
    if size >= MAX_BYTES:
        raise ValueError(f'{path.name}: {size/1024**3:.2f} GiB >= 2 GiB GitHub release limit')
    if path.suffix.lower() == '.zip':
        with ZipFile(path) as archive:
            names = archive.namelist()
            if path.name == 'AQC-Model.zip':
                if not any(name.endswith('/models/medium/model.bin') for name in names):
                    raise ValueError('AQC-Model.zip: missing offline Whisper model.bin')
                if any(not name.startswith('_internal/models/medium/') for name in names):
                    raise ValueError('AQC-Model.zip: unsafe or unexpected model file locations')
            elif not any(name.lower().endswith('.exe') for name in names):
                raise ValueError(f'{path.name}: ZIP does not contain an EXE')
            bad = archive.testzip()
            if bad:
                raise ValueError(f'{path.name}: corrupt ZIP entry {bad}')
    elif path.suffix.lower() == '.exe':
        if path.name != 'AQC-Setup.exe' or path.open('rb').read(2) != b'MZ':
            raise ValueError('Release EXE is not the expected Windows setup executable')
    elif path.suffix.lower() != '.dmg':
        raise ValueError(f'Unsupported release asset type: {path.name}')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4*1024*1024), b''):
            digest.update(block)
    print(f'{path.name}: {size/1024**2:.1f} MiB, SHA256 {digest.hexdigest()}', flush=True)
    return f'{digest.hexdigest()}  {path.name}'


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit('Usage: python scripts/check_release_assets.py file [file ...]')
    paths = [Path(name) for name in sys.argv[1:]]
    lines = [inspect_asset(file) for file in paths]
    for file, line in zip(paths, lines):
        outfile = file.with_name(file.name+'.sha256')
        outfile.write_text(line+'\n', encoding='ascii')
        print('Verified release file:', outfile, flush=True)


if __name__ == '__main__':
    main()
