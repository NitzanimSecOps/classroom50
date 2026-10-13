"""
Grader for 7.1.0.3 "Blowhole's Revenge" — mouse-movement watchdog.

Contract: the student writes  watchdog.py , run as  python3 watchdog.py <device> .
It reads movement packets from <device> (default /dev/input/mice) and appends a
line to  mouse_tracker.log  on movement, but AT MOST one line per 10 seconds.

The grader injects a FIFO as the device and writes synthetic 3-byte packets.
Docstring order = Codo row order.
"""
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
WATCHDOG = Path(os.environ.get("WATCHDOG_PY", ROOT / "watchdog.py"))

PACKET = b"\x08\x05\x02"  # a plausible 3-byte mouse movement packet


def _lines(log):
    if not log.exists():
        return []
    return [ln for ln in log.read_text().splitlines() if ln.strip()]


def _wait_lines(log, n, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if len(_lines(log)) >= n:
            return True
        time.sleep(0.1)
    return len(_lines(log)) >= n


@pytest.fixture
def watchdog(tmp_path):
    """Starts watchdog.py reading a FIFO; yields (write_fd, log_path)."""
    fifo = tmp_path / "mouse"
    os.mkfifo(fifo)
    log = tmp_path / "mouse_tracker.log"

    # Run a copy of the script from tmp so "same dir as the script" == cwd == tmp,
    # whether the student writes the log relative to CWD or to the script's own dir.
    script = tmp_path / "watchdog.py"
    shutil.copy(WATCHDOG, script)

    proc = subprocess.Popen(
        [sys.executable, str(script), str(fifo)],
        cwd=str(tmp_path),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    # Open read-write so this never blocks even if the student's script never
    # opens the device (otherwise a non-opening submission would hang the grader).
    wfd = os.open(str(fifo), os.O_RDWR)

    yield wfd, log

    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        os.close(wfd)
    except OSError:
        pass


def test_1(watchdog):
    """תנועה נרשמת: תנועת עכבר כותבת שורה ללוג"""
    wfd, log = watchdog
    os.write(wfd, PACKET)
    assert _wait_lines(log, 1, timeout=5), \
        "no line was written to mouse_tracker.log after a movement"


def test_2(watchdog):
    """קצב מוגבל: ריבוי תנועות מהירות = שורה אחת לכל היותר"""
    wfd, log = watchdog
    for _ in range(5):
        os.write(wfd, PACKET)
        time.sleep(0.1)
    _wait_lines(log, 1, timeout=5)
    time.sleep(1)
    assert len(_lines(log)) == 1, \
        f"expected exactly 1 line within the 10s window, got {len(_lines(log))}"
