"""Check a REAL safetensors write/read before downloading GigaAM weights."""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import torch
from packaging.version import Version
from safetensors.torch import load_file, save_file

if Version(torch.__version__.split('+')[0]) < Version('2.6'):
    raise RuntimeError('Security: torch >= 2.6 is mandatory for converting .bin')
if Version(np.__version__) < Version('1.26'):
    raise RuntimeError('numpy >= 1.26 is mandatory for safetensors conversion')
with tempfile.TemporaryDirectory(prefix='aqc-safe-preflight-') as root:
    weights = {'regression.weight': torch.arange(6, dtype=torch.float32).reshape(2,3)}
    path = Path(root)/'test.safetensors'
    save_file(weights, str(path))
    recovered = load_file(str(path), device='cpu')
    if not torch.equal(weights['regression.weight'], recovered['regression.weight']):
        raise RuntimeError('safetensors did not round-trip correctly')
print('AQC_SAFE_CONVERSION_PREFLIGHT_OK torch=' + str(torch.__version__) +
      ' numpy=' + np.__version__, flush=True)
