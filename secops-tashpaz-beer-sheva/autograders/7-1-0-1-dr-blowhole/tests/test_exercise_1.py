"""
Grader for 7.1.0.1 "Dr. Blowhole" — zero the log via /dev/zero.

Contract: the student writes  solve.sh , run as  bash solve.sh <file> .
It must overwrite every byte of <file> with 0x00 while keeping its size.
Graded on the resulting file; docstring order = Codo row order.
"""
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
SOLVE = Path(os.environ.get("SOLVE_SH", ROOT / "solve.sh"))
SIZE = 10_000_000


@pytest.fixture
def run_solve(tmp_path):
    def _run():
        target = tmp_path / "access.log"
        target.write_bytes(b"A" * SIZE)  # non-zero original content
        subprocess.run(["bash", str(SOLVE), str(target)],
                       cwd=str(tmp_path), capture_output=True, timeout=60)
        return target
    return _run


def test_1(run_solve):
    """תוכן אופס: כל בייט בקובץ הוא 0x00"""
    target = run_solve()
    data = target.read_bytes()
    assert data, "the file is empty or missing after the script ran"
    assert set(data) == {0}, "not all bytes are 0x00 — the content was not fully zeroed"


def test_2(run_solve):
    """הגודל נשמר: גודל הקובץ זהה למקור"""
    target = run_solve()
    assert target.stat().st_size == SIZE, \
        f"file size changed: expected {SIZE} bytes, got {target.stat().st_size}"
