"""Manual diagnostic, separate from the three normal smoke tests. No ACL edits."""
import ctypes
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parent
HELPER = ROOT / "bin/research-sandbox.exe"
PAYLOAD = ROOT / "bin/boundary/rg.exe"
STARTUP = subprocess.STARTUPINFO()
STARTUP.dwFlags = subprocess.STARTF_USESHOWWINDOW
STARTUP.wShowWindow = subprocess.SW_HIDE


def launch(inputs, mode, timeout):
    return subprocess.Popen(
        [str(HELPER), "--search", str(PAYLOAD), "--input", str(inputs),
         "--query", mode, "--limit", "1", "--timeout-ms", str(timeout), "--output-limit", "1024"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        startupinfo=STARTUP, env={**os.environ, "NATIVE_BOUNDARY_SECRET": "must-not-reach-child"})


def result(process):
    process.wait(timeout=5)
    stdout, stderr = process.communicate()
    assert process.returncode == 0, stderr
    return json.loads(stdout)


def assert_reaped(pid):
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.restype = ctypes.c_void_p
    api.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    api.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    api.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = api.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE
    if handle:
        try:
            assert api.WaitForSingleObject(handle, 2000) == 0, "payload still alive"
        finally:
            api.CloseHandle(handle)
    else:
        assert ctypes.get_last_error() == 87, "cannot prove payload exit"


def test_manual_native_boundary():
    with tempfile.TemporaryDirectory(prefix="boundary-", dir=ROOT / "bin") as directory:
        base = Path(directory)
        inputs = base / "inputs"
        inputs.mkdir()
        (inputs / "existing.txt").write_text("unchanged", encoding="utf-8")
        (base / "outside.txt").write_text("unchanged", encoding="utf-8")
        restricted = result(launch(inputs, "boundary", 3000))
        assert restricted["status"] == "succeeded", restricted
        checks = json.loads(restricted["stdout"])
        assert all(checks.values()), checks
        assert (inputs / "existing.txt").read_text() == "unchanged"
        assert (base / "outside.txt").read_text() == "unchanged"
        timed = result(launch(inputs, "sleep", 300))
        assert timed["status"] == "timeout", timed
        assert_reaped(int(timed["stdout"].strip()))
        cancelled = launch(inputs, "sleep", 15000)
        time.sleep(0.3)
        cancelled.stdin.close()
        cancelled.stdin = None
        cancellation = result(cancelled)
        assert cancellation["status"] == "failed", cancellation
        assert "liveness" in cancellation["error"], cancellation
        assert_reaped(int(cancellation["stdout"].strip()))
        print(json.dumps({"boundary": checks, "timeout": timed["status"], "cancel": cancellation["status"],
                          "payloads_reaped": True}))


if __name__ == "__main__":
    test_manual_native_boundary()
