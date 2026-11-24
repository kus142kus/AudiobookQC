"""Verify a macOS .app advertises and contains *actual* Apple Silicon binaries.

This runs on the Mac CI runner after packaging; does not sign/notarize the app.
Checks Python extension libraries and framework executables, not just .dylib.
"""
from __future__ import annotations
import plistlib
from pathlib import Path
import subprocess
import sys


def candidate_binaries(app: Path, executable: Path):
    found = {executable}
    for pattern in ('*.dylib', '*.so'):
        found.update(app.rglob(pattern))
    # Python 3.11 / Qt / native frameworks can contain extensionless Mach-O
    # binaries in Name.framework/Name, which *.dylib scanning would miss.
    for folder in app.rglob('*.framework'):
        found.add(folder / folder.name.removesuffix('.framework'))
        found.add(folder / 'Versions' / 'Current' / folder.name.removesuffix('.framework'))
    return sorted(f for f in found if f.is_file() and not f.is_symlink())


def audit(app: Path):
    app = Path(app)
    if not app.is_dir() or app.suffix != '.app':
        raise FileNotFoundError(f'Нет macOS APP: {app}')
    plistpath = app / 'Contents' / 'Info.plist'
    data = plistlib.loads(plistpath.read_bytes())
    name = data.get('CFBundleExecutable')
    if not name or '..' in name or '/' in name or '\\' in name:
        raise ValueError('Неверный CFBundleExecutable')
    executable = app / 'Contents' / 'MacOS' / name
    if not executable.is_file():
        raise FileNotFoundError(f'Отсутствует главный исполняемый файл: {executable}')
    checked = 0
    for file in candidate_binaries(app, executable):
        info = subprocess.run(['file', str(file)], text=True,
                              capture_output=True, check=True).stdout
        if 'Mach-O' not in info:
            continue
        archs = subprocess.run(['lipo', '-archs', str(file)], text=True,
                              capture_output=True, check=True).stdout.split()
        if not {'arm64', 'arm64e'}.intersection(archs):
            raise RuntimeError(f'Нет arm64 кода: {file.relative_to(app)} — {archs}')
        checked += 1
    if checked == 0:
        raise RuntimeError('Не найдено ни одного Mach-O файла')
    print(f'AUDIOBOOK_QC_MAC_ARM64_BUNDLE_OK: {checked} binaries, executable {name}', flush=True)

if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: python scripts/macos_bundle_audit.py dist/AudiobookQC.app')
    audit(Path(sys.argv[1]))
