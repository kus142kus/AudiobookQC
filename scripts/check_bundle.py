"""Smoke tests against the actual PyInstaller bundle on the target OS.

Runs inference in separate processes to keep peak RAM lower and identifies
precisely which model or launch stage failed; all failures remain fatal to CI.
"""
from __future__ import annotations
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def verify(executable: Path, flag: str, timeout: int) -> None:
    with tempfile.TemporaryDirectory(prefix="aqc-bundle-test-") as name:
        path = Path(name) / "progress.txt"
        env = dict(os.environ)
        env.update({"AUDIOBOOK_QC_SMOKE_LOG": str(path), "OMP_NUM_THREADS": "2",
                    "MKL_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2",
                    "TOKENIZERS_PARALLELISM": "false", "HF_HUB_OFFLINE": "1",
                    "TRANSFORMERS_OFFLINE": "1"})
        print(f"Verifying {executable.name} {flag}", flush=True)
        try:
            result = subprocess.run([str(executable), flag], env=env,
                                    capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            progress = path.read_text(encoding="utf-8") if path.exists() else "No stage recorded (possibly bootloader or runtime-hook failure)"
            print(progress, flush=True)
            raise RuntimeError(f"{flag} exceeded {timeout}s; last recorded stage shown above") from exc
        progress = path.read_text(encoding="utf-8") if path.exists() else "No stage recorded"
        print(progress, flush=True)
        if result.stdout:
            print(result.stdout.decode("utf-8", "replace")[-3000:], flush=True)
        if result.stderr:
            print(result.stderr.decode("utf-8", "replace")[-3000:], flush=True)
        if result.returncode:
            raise RuntimeError(f"{flag} failed with exit code {result.returncode}")


def main() -> None:
    exe = Path(sys.argv[1]).resolve()
    assert exe.is_file(), f"Executable missing: {exe}"
    # GUI first: on a broken runtime, fail in seconds, not after loading all ML models.
    for flag, timeout in (("--smoke-gui", 120),
                          ("--self-test", 180),
                          ("--test-whisper", 480),
                          ("--test-gigaam", 480),
                          ("--test-events", 180)):
        verify(exe, flag, timeout)
    print("AUDIOBOOK_QC_NATIVE_DESKTOP_OK", flush=True)


if __name__ == "__main__":
    main()
