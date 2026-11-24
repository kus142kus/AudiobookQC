"""Release gate: fail on oversized or corrupt packages, emit SHA256SUMS.

GitHub Release assets are limited to <2 GiB each (not 2 GB decimal).
Does not depend on the application or installed speech models.
"""
from __future__ import annotations
import hashlib
from pathlib import Path
import sys
import zipfile

MAX_BYTES = 2 * 1024**3


def inspect_asset(path: Path, *, check_zip=True) -> str:
    path = Path(path)
    if not path.is_file() or path.stat().st_size == 0:
        raise FileNotFoundError(f'Отсутствует готовый пакет: {path}')
    size = path.stat().st_size
    if size >= MAX_BYTES:
        raise ValueError(f'{path.name}: {size/1024**3:.2f} GiB >= 2 GiB, GitHub Releases не примет файл. '
                         'Требуется отдельно оптимизировать упаковку или делить дистрибутив.')
    if path.suffix.lower() == '.zip' and check_zip:
        with zipfile.ZipFile(path) as bundle:
            if not any(name.lower().endswith('.exe') for name in bundle.namelist()):
                raise ValueError(f'{path.name}: ZIP не содержит EXE')
            broken = bundle.testzip()
            if broken is not None:
                raise ValueError(f'{path.name}: повреждённый файл в ZIP: {broken}')
    if path.suffix.lower() not in ('.zip', '.dmg'):
        raise ValueError('Ожидается ZIP или DMG: '+path.name)
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024*1024), b''):
            digest.update(block)
    print(f'{path.name}: {size/1024**2:.1f} MiB, SHA256 {digest.hexdigest()}', flush=True)
    return f'{digest.hexdigest()}  {path.name}'


def main():
    if len(sys.argv) < 2:
        raise SystemExit('Usage: python scripts/check_release_assets.py dist/file.zip [file.dmg ...]')
    files = [Path(s) for s in sys.argv[1:]]
    lines = [inspect_asset(f) for f in files]
    outfile = files[0].parent / (files[0].name + '.sha256')
    outfile.write_text('\n'.join(lines)+'\n', encoding='ascii')
    print('Release asset gate passed; checksums:', outfile, flush=True)

if __name__ == '__main__':
    main()
