"""Run a packed executable on its own OS and verify key runtime dependencies and localhost UI."""
import http.client
import socket
import subprocess
import sys
import time
from pathlib import Path

exe = Path(sys.argv[1]).resolve()
assert exe.is_file(), f"Not found: {exe}"
print("Checking bundle:", exe, flush=True)
preflight = subprocess.run([str(exe), "--self-test"], timeout=80, check=False)
if preflight.returncode != 0:
    raise RuntimeError(f"Bundled dependency preflight failed with exit code {preflight.returncode}")
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
proc = subprocess.Popen([str(exe), "--no-browser", "--port", str(port)])
try:
    for attempt in range(120):
        if proc.poll() is not None:
            raise RuntimeError(f"App exited during startup, returncode {proc.returncode}")
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            conn.request("GET", "/")
            r=conn.getresponse()
            page=r.read()
            conn.close()
            if r.status != 200 or b"html" not in page.lower():
                raise RuntimeError(f"Bad HTML UI response: {r.status}")
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            conn.request("GET", "/api/projects")
            r=conn.getresponse(); data=r.read(); conn.close()
            if r.status != 200 or not data.startswith(b"["):
                raise RuntimeError(f"Bad API response: {r.status}, {data[:80]!r}")
            print("BUNDLE_SMOKE_TEST_OK", flush=True)
            break
        except (ConnectionError, TimeoutError, OSError):
            time.sleep(.5)
    else:
        raise RuntimeError("App HTTP UI did not become available")
    conn=http.client.HTTPConnection("127.0.0.1",port,timeout=5)
    conn.request("POST","/api/shutdown",body=b"{}",headers={"Content-Type":"application/json"})
    conn.getresponse().read();conn.close()
    proc.wait(timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(f"App closed with nonzero return code: {proc.returncode}")
finally:
    if proc.poll() is None:
        proc.terminate()
        try: proc.wait(timeout=10)
        except subprocess.TimeoutExpired: proc.kill()
