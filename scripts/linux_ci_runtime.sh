#!/usr/bin/env bash
# Shared Ubuntu 24.04 runtime dependency setup for ALL Linux previews.
# Keep this file common so x64 and ARM64 cannot silently diverge.
set -euo pipefail
if [[ "$(uname -s)" != "Linux" ]]; then
  echo "AQC_LINUX_RUNTIME_ERROR: not Linux" >&2
  exit 1
fi
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends \
  xvfb xauth libegl1 libgl1 libopengl0 libxkbcommon-x11-0 \
  libxcb-cursor0 libxcb-xinerama0 libxcb-icccm4 libxcb-image0 \
  libxcb-keysyms1 libxcb-render-util0 libxcb-xkb1 libxcb-randr0 \
  libxcb-shape0 libxcb-xfixes0 libxcb-sync1 libx11-xcb1 \
  libnss3 libgbm1 libasound2t64 libdbus-1-3 \
  libportaudio2 libminizip1t64
python - <<'PYCODE'
import ctypes
for name in ('libportaudio.so.2', 'libminizip.so.1', 'libxcb-cursor.so.0'):
    try:
        ctypes.CDLL(name)
    except OSError as e:
        raise SystemExit(f'AQC_LINUX_RUNTIME_MISSING {name}: {e}') from e
    print(f'AQC_LINUX_RUNTIME_OK {name}', flush=True)
PYCODE
