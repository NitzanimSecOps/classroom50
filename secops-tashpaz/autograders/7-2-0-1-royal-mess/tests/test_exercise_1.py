"""
Grader for 7.2.0.1 "Royal Mess" — process-triage incident response.

The chaos + the student's cleanup.sh run INSIDE a docker container (its own PID
namespace), so the student's system-wide process kills can't escape the sandbox —
worst case is a failed grade, never a killed host process. (Rootless `unshare` is
blocked on the fleet by AppArmor, so docker is the isolation, same as the ARP grader.)

run_scenarios.py (mounted into the container) reproduces four messes + a legit
"royal_db" bystander that MUST survive, runs cleanup.sh, and prints the measured
final state as JSON. One sandbox run per module; docstring order = Codo row order.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
IMAGE = os.environ.get("ROYAL_IMAGE", "royal-grader:latest")


@pytest.fixture(scope="module")
def outcome():
    r = subprocess.run(
        ["docker", "run", "--rm", "--network", "none",
         "-v", f"{ROOT}:/work:ro", "-w", "/work", IMAGE,
         "python3", "run_scenarios.py", "/work/cleanup.sh"],
        capture_output=True, text=True, timeout=180)
    lines = [l for l in r.stdout.splitlines() if l.strip().startswith("{")]
    assert lines, (
        "the chaos sandbox produced no result — the cleanup may have killed the "
        f"harness.\n--- stdout ---\n{r.stdout}\n--- stderr ---\n{r.stderr}")
    return json.loads(lines[-1])


def test_1(outcome):
    """שחרור הפורט: הפורט של האתר המלכותי פנוי"""
    assert outcome["port_free"], "the royal-site port is still held"


def test_2(outcome):
    """חיסול זולל המעבד: תהליך ה-CPU הגבוה נהרג"""
    assert outcome["hog_dead"], "the CPU-hog process is still running"


def test_3(outcome):
    """עצירת המבול: תהליכי ה-curl וההורה שמייצר אותם נהרגו"""
    assert outcome["flood_gone"], "curl floods still present (did you kill the parent script?)"


def test_4(outcome):
    """קבירת הזומבים: לא נותרו תהליכי zombie"""
    assert outcome["zombies"] == 0, f"{outcome['zombies']} zombie process(es) remain"


def test_5(outcome):
    """ללא נזק היקפי: השירות התקין (royal_db) עדיין רץ"""
    assert outcome["bystander_alive"], "the legit royal_db service was killed — don't nuke everything"
