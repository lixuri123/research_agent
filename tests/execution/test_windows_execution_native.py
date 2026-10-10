"""Small native smoke suite; these do not prove every Windows boundary."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


HELPER = Path(__file__).resolve().parents[2] / "native/windows-sandbox/bin/research-sandbox.exe"
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows native helper")


def run_helper(*args):
    assert HELPER.is_file(), "build native/windows-sandbox/build.ps1 first"
    startup = subprocess.STARTUPINFO()
    startup.dwFlags = subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    with subprocess.Popen([str(HELPER), *map(str, args)], stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          startupinfo=startup) as process:
        process.wait(timeout=20)  # Keep the parent-liveness pipe open until exit.
        stdout, stderr = process.communicate()
    assert process.returncode == 0, stderr.decode("utf-8", "replace")
    return json.loads(stdout.decode("utf-8"))


def test_native_probe():
    result = run_helper("--probe")
    assert result["status"] == "succeeded", result
    assert result["protocol"] == 1
    assert result["enforcement"] == "partial"
    assert result["file_read"] == result["network"] == "not_isolated"


def test_native_search_literal_unicode_and_bounded_output(tmp_path):
    (tmp_path / "notes.txt").write_text(("--中文 literal\n" * 1000), encoding="utf-8")
    result = run_helper("--search", shutil.which("rg"), "--input", tmp_path,
                        "--query", "--中文", "--limit", 50, "--timeout-ms", 5000,
                        "--output-limit", 1024)
    assert result["status"] == "succeeded", result
    assert result["exit_code"] == 0
    assert "--中文" in result["stdout"]
    assert result["truncated"] is True
    assert len(result["stdout"].encode()) + len(result["stderr"].encode()) <= 1024


def test_native_rejects_unexpected_flags():
    result = run_helper("--probe", "--command", "cmd.exe")
    assert result["status"] == "failed"
    assert result["exit_code"] is None
    assert result["error"]
