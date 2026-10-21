"""6.1.0.4 Man in the Middle — grading (blackbox).

The sandbox launches the student's main.py in the attacker namespace and measures the
full MITM from outside the code. autograder.py just runs pytest; this fixture shells into
Docker, which builds the netns lab (needs privileges the runner user lacks) with no
network — which is also what stops the student's code from reaching the internet at grade
time.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import tempfile

import pytest

IMAGE = os.environ.get("ARP_GRADER_IMAGE", "arp-grader:latest")
HARNESS = pathlib.Path(__file__).resolve().parent
TIMEOUT = int(os.environ.get("ARP_GRADER_TIMEOUT", "240"))


def _work_dir() -> str:
    """The dir mounted into the sandbox as /work. In the classroom50 bundle everything is
    already flat next to this test (tests + grader modules + the student's files). In the
    tashpaz tree the grader modules live in a grader/ subdir, so flatten them next to the
    top-level .py into a temp dir for local runs."""
    grader = HARNESS / "grader"
    if not grader.is_dir():
        return str(HARNESS)
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="arp-grade-"))
    for f in list(HARNESS.glob("*.py")) + list(grader.glob("*.py")):
        shutil.copy2(f, tmp / f.name)
    return str(tmp)


@pytest.fixture(scope="module")
def results() -> dict:
    mount = _work_dir()
    proc = subprocess.run(
        ["docker", "run", "--rm", "--privileged", "--network", "none",
         "-v", f"{mount}:/work:ro", "-w", "/work", IMAGE,
         "python3", "run_scenarios.py", "/work"],
        capture_output=True, text=True, timeout=TIMEOUT)
    lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip().startswith("{")]
    if not lines:
        pytest.fail("grading sandbox produced no result JSON.\n"
                    f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr[-1500:]}")
    return json.loads(lines[-1])


def _check(results: dict, key: str) -> None:
    entry = results.get(key, {})
    assert entry.get("ok"), entry.get("detail", f"scenario {key} did not pass")


def test_emits_forged_arp(results):
    """Emits forged ARP replies onto the segment."""
    _check(results, "emits_arp_replies")


def test_poisons_the_victim(results):
    """Poisons the victim's ARP cache for the gateway."""
    _check(results, "poisons_victim")


def test_poisons_the_gateway(results):
    """Poisons the gateway's ARP cache for the victim."""
    _check(results, "poisons_gateway")


def test_intercepts_victim_to_gateway_traffic(results):
    """Sits on the path: victim->gateway traffic passes through the attacker."""
    _check(results, "intercepts_traffic")


def test_preserves_connectivity_true_mitm(results):
    """Keeps the victim connected while poisoned (a real MITM, not a denial of service)."""
    _check(results, "preserves_connectivity")
