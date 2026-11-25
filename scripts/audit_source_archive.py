"""Check the EXACT source ZIP delivered to GitHub, including hidden .github.

This is deliberately stdlib-only. It detects missing/duplicate/stale workflow
files, unsafe archive names, and accidentally bundled private audio/manuscripts.
It also executes the release contract from a fresh extraction rather than from
whichever working copy happened to pass tests earlier.
"""
from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys
import tempfile
import zipfile

EXPECTED = {
    'AudiobookQC/.github/workflows/build.yml',
    'AudiobookQC/scripts/build_installer.py',
    'AudiobookQC/scripts/verify_release_contract.py',
    'AudiobookQC/installer/AudiobookQC.iss.in',
    'AudiobookQC/.github/workflows/linux-preview.yml',
    'AudiobookQC/.github/workflows/linux-arm64-preview.yml',
    'AudiobookQC/run.py',
    'AudiobookQC/requirements-build.txt',
    'AudiobookQC/tests/test_release_version_single_source.py',
}
PRIVATE = {'.mp3','.wav','.m4a','.flac','.mp4','.docx','.pdf','.db','.sqlite','.sqlite3','.aac','.ogg'}


def audit(path: pathlib.Path) -> int:
    if not path.is_file():
        raise FileNotFoundError(path)
    with zipfile.ZipFile(path) as f:
        details = f.infolist()
        names = [info.filename for info in details]
        if len(names) != len(set(names)):
            raise ValueError('Duplicate names in source ZIP')
        present=set(names)
        missing=EXPECTED-present
        if missing:
            raise ValueError(f'Source ZIP is missing vital files (especially hidden .github): {sorted(missing)}')
        for item in details:
            p=pathlib.PurePosixPath(item.filename)
            if (not item.filename.startswith('AudiobookQC/') or
                item.filename.startswith('/') or '\\' in item.filename or
                any(x in ('.','..') for x in item.filename.split('/')) or
                (item.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError(f'Unsafe ZIP entry: {item.filename}')
            if not item.is_dir() and p.suffix.lower() in PRIVATE:
                raise ValueError(f'Private media/manuscript file included: {item.filename}')
        if f.testzip() is not None:
            raise ValueError('Source ZIP CRC failure')
        with tempfile.TemporaryDirectory(prefix='aqc-source-audit-') as tmp:
            f.extractall(tmp)
            root=pathlib.Path(tmp)/'AudiobookQC'
            call=subprocess.run([sys.executable,str(root/'scripts/verify_release_contract.py')],
                                cwd=root,capture_output=True,text=True,timeout=30)
            if call.returncode:
                raise RuntimeError(f'Source ZIP failed release contract after extraction:\n{call.stdout}{call.stderr}')
            print(call.stdout.strip(),flush=True)
    print(f'AUDIOBOOK_QC_SOURCE_ZIP_OK: {len(names)} paths, hidden workflow present, no private media',flush=True)
    return len(names)


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive',type=pathlib.Path)
    args=parser.parse_args()
    audit(args.archive)

if __name__ == '__main__':
    main()
