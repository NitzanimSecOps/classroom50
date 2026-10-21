"""
Grader for 7.2.0.2 "Self Destruct" (signal-trap exercise).

Each test launches the student's  service.py  in an isolated working
directory, drives it with a signal, and asserts on the FINAL STATE
(files on disk + the process's exit) — never on how the script is written.

Test order is load-bearing: the docstring of each test is its Codo row
label, so the rows must stay in this positional order.
"""
import os
import signal
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
SERVICE = Path(os.environ.get("SERVICE_PY", ROOT / "service.py"))

LAUNCH_TIMEOUT = 5    # seconds for the lock file to appear after start (should be instant)
EXIT_TIMEOUT = 8      # seconds for the process to exit after a signal


def _wait(pred, timeout, interval=0.1):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(interval)
    return pred()


def _alive(p):
    return p.poll() is None


def _term_group(p):
    try:
        os.killpg(os.getpgid(p.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        p.wait(timeout=5)
    except Exception:
        pass


@pytest.fixture
def run_service(tmp_path):
    """Yields a launcher; each call starts a fresh service in tmp_path."""
    procs = []

    def _start():
        p = subprocess.Popen(
            [sys.executable, str(SERVICE)],
            cwd=str(tmp_path),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # own process group, so we can clean up
        )
        procs.append(p)
        return p, tmp_path

    yield _start
    for p in procs:
        _term_group(p)


def test_1(run_service):
    """רישום הרצה: השירות יוצר קובץ נעילה בעלייה"""
    p, wd = run_service()
    lock = wd / "service.lock"
    assert _wait(lambda: lock.exists(), LAUNCH_TIMEOUT), \
        "service.lock was not created on startup"


def test_2(run_service):
    """שירות מתמשך: התהליך נשאר חי עד לקבלת סיגנל"""
    p, wd = run_service()
    lock = wd / "service.lock"
    assert _wait(lambda: lock.exists(), LAUNCH_TIMEOUT), "service.lock was not created"
    time.sleep(2)
    assert _alive(p), "service exited on its own before any signal was sent"
    assert lock.exists(), "lock file disappeared while the service was still running"


def test_3(run_service):
    """כיבוי מסודר: SIGTERM מפעיל ניקוי וסיום תקין"""
    p, wd = run_service()
    lock, log = wd / "service.lock", wd / "service.log"
    assert _wait(lambda: lock.exists(), LAUNCH_TIMEOUT), "service.lock was not created"

    p.terminate()  # SIGTERM
    assert _wait(lambda: not _alive(p), EXIT_TIMEOUT), "service did not exit after SIGTERM"

    assert not lock.exists(), "service.lock was not removed during graceful shutdown"
    text = log.read_text() if log.exists() else ""
    assert "shutdown complete" in text, "service.log is missing the 'shutdown complete' line"
    assert str(date.today().year) in text, \
        "the shutdown line should include the current date (use datetime)"
    assert p.returncode == 0, \
        f"expected a clean exit (0) after handling SIGTERM, got exit code {p.returncode}"


def test_4(run_service):
    """אין קיצורי דרך: SIGKILL לא מריץ ניקוי"""
    p, wd = run_service()
    lock, log = wd / "service.lock", wd / "service.log"
    assert _wait(lambda: lock.exists(), LAUNCH_TIMEOUT), "service.lock was not created"

    p.kill()  # SIGKILL — cannot be trapped
    assert _wait(lambda: not _alive(p), EXIT_TIMEOUT), "service did not die after SIGKILL"

    assert lock.exists(), \
        "lock file should still be there after SIGKILL — no handler can run on -9"
    assert not (log.exists() and "shutdown complete" in log.read_text()), \
        "'shutdown complete' must NOT appear after SIGKILL (was cleanup faked at startup?)"
