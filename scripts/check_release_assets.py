"""Audit GitHub release assets, create or independently verify SHA-256 sidecars.

CRITICAL: --verify is read-only and MUST be used when publishing downloaded
artifacts. Regenerating .sha256 in a publishing job would erase evidence of
artifact substitution between build and release.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path, PurePosixPath
import re
from zipfile import ZipFile, BadZipFile

MAX_BYTES = 2 * 1024**3  # GitHub release asset limit (each individual file)
SHA256_RE = re.compile(r'^([a-f0-9]{64})  ([^\r\n/\\]+)\n?$')


def _safe_archive_entry(value: str) -> bool:
    if not value or '\\' in value or value.startswith('/') or '\x00' in value:
        return False
    parts = PurePosixPath(value).parts
    return bool(parts) and all(part not in ('.', '..') for part in value.split('/')) and ':' not in parts[0]


def _verify_zip(path: Path) -> None:
    try:
        with ZipFile(path) as archive:
            files = archive.infolist()
            names = [entry.filename for entry in files]
            if len(names) != len(set(names)):
                raise ValueError(f'{path.name}: duplicate archive paths detected')
            for entry in files:
                if not _safe_archive_entry(entry.filename):
                    raise ValueError(f'{path.name}: unsafe archive entry: {entry.filename!r}')
                if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError(f'{path.name}: symlinks are not allowed in model ZIP')
                if entry.flag_bits & 0x1:
                    raise ValueError(f'{path.name}: encrypted archive entry')
            if path.name == 'AQC-Model.zip':
                if '_internal/models/medium/model.bin' not in names:
                    raise ValueError('AQC-Model.zip: offline Whisper model.bin missing')
                if any(not name.startswith('_internal/models/medium/') for name in names):
                    raise ValueError('AQC-Model.zip: unexpected model file locations')
            elif not any(name.lower().endswith('.exe') for name in names):
                raise ValueError(f'{path.name}: ZIP does not contain a Windows EXE')
            if archive.testzip() is not None:
                raise ValueError(f'{path.name}: ZIP failed CRC integrity')
    except BadZipFile as exc:
        raise ValueError(f'{path.name}: invalid ZIP archive') from exc


def inspect_asset(path: Path) -> str:
    path = Path(path)
    if not path.is_file() or path.stat().st_size == 0:
        raise FileNotFoundError(f'Missing release asset: {path}')
    size = path.stat().st_size
    if size >= MAX_BYTES:
        raise ValueError(f'{path.name}: {size/1024**3:.2f} GiB >= 2 GiB release limit')
    if path.suffix.lower() == '.zip':
        _verify_zip(path)
    elif path.suffix.lower() == '.exe':
        with path.open('rb') as stream:
            if path.name != 'AQC-Setup.exe' or stream.read(2) != b'MZ':
                raise ValueError('Release EXE is not the expected Windows setup executable')
    elif path.suffix.lower() != '.dmg':
        raise ValueError(f'Unsupported release asset type: {path.name}')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4*1024*1024), b''):
            digest.update(block)
    print(f'{path.name}: {size/1024**2:.1f} MiB, SHA256 {digest.hexdigest()}', flush=True)
    return f'{digest.hexdigest()}  {path.name}'


def verify_sidecar(path: Path, computed: str) -> None:
    sidecar = path.with_name(path.name + '.sha256')
    original = sidecar.read_text(encoding='ascii')
    match = SHA256_RE.fullmatch(original)
    if match is None or match.group(2) != path.name:
        raise ValueError(f'Invalid checksum sidecar format: {sidecar}')
    if original.strip() != computed.strip():
        raise ValueError(f'Checksum mismatch: {path.name} has changed since build. RELEASE BLOCKED.')
    print(f'RELEASE_CHECKSUM_VERIFIED {path.name}', flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Verify release assets and SHA-256')
    parser.add_argument('--verify', action='store_true',
                        help='Verify existing build SHA-256 files, never overwrite them')
    parser.add_argument('files', nargs='+', type=Path)
    args = parser.parse_args(argv)
    for path in args.files:
        line = inspect_asset(path)
        if args.verify:
            verify_sidecar(path, line)
        else:
            outfile = path.with_name(path.name + '.sha256')
            outfile.write_text(line+'\n', encoding='ascii')
            print(f'Generated checksum: {outfile}', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
