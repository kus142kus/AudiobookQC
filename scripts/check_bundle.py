"""Validate the actual packaged *desktop* executable on the target OS."""
from __future__ import annotations
import subprocess
import sys
from pathlib import Path

exe = Path(sys.argv[1]).resolve()
assert exe.is_file(), f'No executable: {exe}'
for flag, timeout in (('--self-test', 600), ('--smoke-gui', 120)):
    print('Verifying',exe.name,flag,flush=True)
    proc = subprocess.run([str(exe), flag], timeout=timeout, check=False)
    if proc.returncode:
        raise RuntimeError(f'{flag} failed: exit code {proc.returncode}')
print('AUDIOBOOK_QC_NATIVE_DESKTOP_OK',flush=True)
