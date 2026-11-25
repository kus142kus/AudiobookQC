"""Print native library dependencies on a failed Linux Qt smoke test.
Never makes a failed GUI check succeed; for CI diagnostics only.
"""
from __future__ import annotations

import ctypes
import platform
import subprocess
from pathlib import Path

print('AQC_QT_DIAG_SYSTEM', platform.platform(), flush=True)
for lib in ('libminizip.so.1', 'libportaudio.so.2', 'libxcb-cursor.so.0'):
    try:
        ctypes.CDLL(lib)
        print('AQC_QT_DIAG_LIB_OK', lib, flush=True)
    except OSError as exc:
        print('AQC_QT_DIAG_LIB_MISSING', lib, str(exc), flush=True)
try:
    import PySide6
    package = Path(PySide6.__file__).resolve().parent
    print('AQC_QT_DIAG_PYSIDE', package, flush=True)
    candidates = [package/'Qt/plugins/platforms/libqxcb.so']
    candidates += list((package/'Qt/lib').glob('libQt6WebEngineCore*.so*'))
    for path in candidates[:4]:
        if not path.is_file():
            print('AQC_QT_DIAG_FILE_MISSING', path, flush=True)
            continue
        print('AQC_QT_DIAG_LDD', path, flush=True)
        result = subprocess.run(['ldd', str(path)], capture_output=True, text=True,
                                errors='replace', check=False)
        print(result.stdout[-14000:], flush=True)
        print(result.stderr[-2000:], flush=True)
except Exception as exc:
    print('AQC_QT_DIAG_ERROR', repr(exc), flush=True)
