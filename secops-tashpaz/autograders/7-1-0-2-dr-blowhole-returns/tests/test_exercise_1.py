"""
Grader for 7.1.0.2 "Dr. Blowhole Returns" — scramble the key via /dev/random.

Contract: the student writes  solve.sh , run as  bash solve.sh <file> . It must
  1. print   Key Length: <x> characters, expected 100.
  2. overwrite <file> with 100 random, printable ASCII characters
  3. print   Successfully ruined <file>
Graded on stdout + the resulting file; docstring order = Codo row order.
"""
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
SOLVE = Path(os.environ.get("SOLVE_SH", ROOT / "solve.sh"))
ORIG_LEN = 100


@pytest.fixture
def run_solve(tmp_path):
    def _run():
        target = tmp_path / "secret.key"
        orig = os.urandom(ORIG_LEN)  # binary (non-printable) original
        target.write_bytes(orig)
        r = subprocess.run(["bash", str(SOLVE), str(target)],
                           cwd=str(tmp_path), capture_output=True, timeout=60)
        out = r.stdout.decode(errors="replace")
        return target, orig, out
    return _run


def test_1(run_solve):
    """אימות אורך: הדפסת Key Length תקינה"""
    target, orig, out = run_solve()
    assert "Key Length: 100 characters, expected 100" in out, \
        "stdout is missing 'Key Length: 100 characters, expected 100.'"


def test_2(run_solve):
    """מפתח קריא: 100 תווים דפיסים"""
    target, orig, out = run_solve()
    data = target.read_bytes()
    assert len(data) == ORIG_LEN, \
        f"key length is {len(data)}, expected {ORIG_LEN}"
    assert all(0x20 <= b <= 0x7E for b in data), \
        "the key contains non-printable bytes — it must be printable ASCII"


def test_3(run_solve):
    """אקראיות: התוכן השתנה ובעל אנטרופיה"""
    target, orig, out = run_solve()
    data = target.read_bytes()
    assert data != orig, "the key was not changed"
    assert len(set(data)) >= 10, \
        "the key does not look random (too few distinct characters)"


def test_4(run_solve):
    """הודעת הצלחה: הדפסת Successfully ruined"""
    target, orig, out = run_solve()
    assert "Successfully ruined" in out and (str(target) in out or target.name in out), \
        "stdout is missing 'Successfully ruined <file>'"
