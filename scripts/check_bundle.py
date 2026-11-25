"""Test an actual frozen AudiobookQC executable, without pipe-based GUI deadlocks.

The checker waits for the exact PyInstaller process and records output in a file.
A GUI helper inheriting stdout/stderr cannot keep subprocess.communicate() stuck.
The macOS GUI check is retried once only when it timed out BEFORE emitting any
application-side stage marker; real GUI/model failures remain fatal.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path


MODEL_FLAGS = ("--test-whisper", "--test-gigaam", "--test-events")


def _terminate_process_tree(proc: subprocess.Popen) -> None:
    """Stop a timed-out frozen GUI and any subprocesses in its own session."""
    if os.name == "posix" and sys.platform != "darwin":
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError as exc:
            print(f"AQC_PROBE_TERMINATE_WARNING: {exc}", flush=True)
    else:
        try:
            proc.terminate()
        except OSError:
            pass
    try:
        proc.wait(timeout=4)
    except subprocess.TimeoutExpired:
        if os.name == "posix" and sys.platform != "darwin":
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        proc.kill()
        proc.wait(timeout=5)


def _mac_hang_diagnostics(pid: int, destination: Path) -> None:
    """Collect native stacks when permitted; lack of permission is not success."""
    if sys.platform != "darwin":
        return
    try:
        result = subprocess.run(
            ["/bin/ps", "-p", str(pid), "-o", "pid,ppid,state,etime,command"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=5, check=False,
        )
        print("AQC_MAC_PROCESS_STATE:\n" + result.stdout[-3000:], flush=True)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"AQC_MAC_PS_UNAVAILABLE: {exc}", flush=True)
    try:
        result = subprocess.run(
            ["/usr/bin/sample", str(pid), "2", "-file", str(destination)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=12, check=False,
        )
        if destination.is_file():
            sample = destination.read_text(encoding="utf-8", errors="replace")
            # Only the useful start of the stacks; GitHub logs have output limits.
            print("AQC_MAC_HANG_SAMPLE:\n" + sample[:9000], flush=True)
        else:
            print(f"AQC_MAC_SAMPLE_UNAVAILABLE rc={result.returncode}: {result.stdout[-1000:]}", flush=True)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"AQC_MAC_SAMPLE_UNAVAILABLE: {exc}", flush=True)


def _one_attempt(executable: Path, flag: str, timeout: int, folder: Path, number: int) -> tuple[int | None, bool, str]:
    progress_file = folder / f"progress-{number}.txt"
    output_file = folder / f"output-{number}.txt"
    env = dict(os.environ)
    env.update({
        "AUDIOBOOK_QC_SMOKE_LOG": str(progress_file),
        "OMP_NUM_THREADS": "2", "MKL_NUM_THREADS": "2",
        "OPENBLAS_NUM_THREADS": "2", "TOKENIZERS_PARALLELISM": "false",
        "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
        "PYTHONUNBUFFERED": "1", "PYTHONFAULTHANDLER": "1",
    })
    print(f"AQC_PROBE_START {executable.name} {flag} attempt={number} timeout={timeout}s", flush=True)
    # IMPORTANT: use a file, never capture_output=True / PIPE.  Native macOS
    # WKWebView helpers may inherit pipe handles after the parent exits.
    with output_file.open("wb") as output:
        proc = subprocess.Popen(
            [str(executable), flag], env=env,
            stdout=output, stderr=subprocess.STDOUT,
            # Keep macOS GUI launch semantics identical to the proven build.
            start_new_session=(os.name == "posix" and sys.platform != "darwin"),
        )
        timed_out = False
        try:
            returncode = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            returncode = None
            print(f"AQC_PROBE_TIMEOUT {flag} attempt={number} pid={proc.pid}", flush=True)
            _mac_hang_diagnostics(proc.pid, folder / f"mac-sample-{number}.txt")
            _terminate_process_tree(proc)
        finally:
            output.flush()
    progress = (progress_file.read_text(encoding="utf-8", errors="replace")
                if progress_file.exists() else "")
    output_text = output_file.read_text(encoding="utf-8", errors="replace")
    print("AQC_APP_STAGES:\n" + (progress[-12000:] or "No stage recorded (possibly bootloader, runtime hook, or GUI initialization)"), flush=True)
    if output_text:
        print("AQC_APP_OUTPUT:\n" + output_text[-6000:], flush=True)
    return returncode, timed_out, progress


def _verify_windows_unchanged(executable: Path, flag: str, timeout: int) -> None:
    """Preserve the exact Windows x64 checking behavior that passed build #41."""
    with tempfile.TemporaryDirectory(prefix="aqc-bundle-test-") as dirname:
        path = Path(dirname) / "progress.txt"
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


def verify(executable: Path, flag: str, timeout: int) -> None:
    if os.name == "nt":
        return _verify_windows_unchanged(executable, flag, timeout)
    max_attempts = 2 if (sys.platform == "darwin" and flag == "--smoke-gui") else 1
    with tempfile.TemporaryDirectory(prefix="aqc-bundle-test-") as dirname:
        folder = Path(dirname)
        for attempt in range(1, max_attempts + 1):
            # The second attempt allows a slower first-run macOS security check.
            limit = max(timeout, 180) if attempt == 2 else timeout
            code, timed_out, progress = _one_attempt(executable, flag, limit, folder, attempt)
            if code == 0 and not timed_out:
                print(f"AQC_PROBE_OK {flag} attempt={attempt}", flush=True)
                return
            if timed_out and attempt < max_attempts and not progress.strip():
                print("AQC_MAC_PRE_ENTRY_TIMEOUT: one bounded fresh-process retry", flush=True)
                continue
            if timed_out:
                raise RuntimeError(f"{flag} exceeded {limit}s (attempt {attempt}/{max_attempts}); see native process diagnostics")
            raise RuntimeError(f"{flag} failed with exit code {code} (attempt {attempt}/{max_attempts})")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/check_bundle.py PATH_TO_FROZEN_EXECUTABLE")
    executable = Path(sys.argv[1]).resolve()
    if not executable.is_file():
        raise FileNotFoundError(f"Executable missing: {executable}")
    # GUI first: do not download models or announce success when a real GUI fails.
    for flag, timeout in (("--smoke-gui", 120),
                          ("--self-test", 180),
                          ("--test-whisper", 480),
                          ("--test-gigaam", 480),
                          ("--test-events", 180)):
        verify(executable, flag, timeout)
    print("AUDIOBOOK_QC_NATIVE_DESKTOP_OK", flush=True)


if __name__ == "__main__":
    main()
