"""6.1.0.2 ARP Request — grading.

Not blackbox: the sandbox imports the student's resolve_ip and calls it in the live lab,
then checks the MAC it returns. Returning the right MAC proves the request was sent AND
the reply was parsed. autograder.py just runs pytest; this fixture shells into Docker,
which builds the netns lab (needs privileges the runner user lacks) with no network.
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
TIMEOUT = int(os.environ.get("ARP_GRADER_TIMEOUT", "180"))


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


def test_resolves_the_gateway(results):
    """resolve_ip returns the gateway's real MAC (sent a request and parsed the reply)."""
    _check(results, "resolves_gateway")


def test_resolves_the_victim(results):
    """resolve_ip returns the victim's real MAC (sent a request and parsed the reply)."""
    _check(results, "resolves_victim")
