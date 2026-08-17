#!/usr/bin/env python3
"""classroom50 bundle entrypoint — grade a pytest exercise. Generic: identical for
every exercise, generated into each assignment bundle by apply_classroom50.py.

How the runner invokes us (runner.py run_entrypoint / finalize_result):
  * `python <bundle>/autograder.py` with **cwd = the student checkout**
  * we must write `result.json` into cwd and exit 0
  * the runner overwrites owner / assignment_type / datetime / graded_at afterwards,
    so we don't attempt to author those authoritatively

Why the tests live here and not in the student repo: a declarative tests.json can only
run commands with cwd=workspace and is never told where the bundle was extracted, so
bundled tests are unreachable from it. An entrypoint knows its own location, so the
tests can stay in the bundle — off the student's machine, unreadable and un-editable.

One result row per pytest test (not one lumped score) so Codo shows real test names:
Codo pairs submissions.tests_score[i] with the task's test at index i.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile

BUNDLE = pathlib.Path(__file__).resolve().parent
WORKSPACE = pathlib.Path.cwd()
TESTS_DIR = BUNDLE / "tests"
META = json.loads((BUNDLE / "meta.json").read_text(encoding="utf-8"))


def ensure_deps() -> None:
    """pytest + the json report plugin. The grading runner is ephemeral, so this is a
    fresh install per job; quiet unless it fails."""
    try:
        import pytest_jsonreport  # noqa: F401
        import pytest  # noqa: F401
        return
    except ImportError:
        pass
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet",
                    "--disable-pip-version-check", "pytest", "pytest-json-report"],
                   check=False)


def run_pytest() -> dict:
    out = pathlib.Path(tempfile.mkdtemp(prefix="c50-pytest-")) / "report.json"
    env = dict(os.environ)
    # The tests import `solutions.suite_X.exercise_Y.solution` — that package is the
    # STUDENT's code in the workspace, so the workspace must lead sys.path while the
    # test files themselves come from the bundle.
    env["PYTHONPATH"] = os.pathsep.join(
        [str(WORKSPACE)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    subprocess.run(
        [sys.executable, "-m", "pytest", str(TESTS_DIR), "-q", "--no-header",
         "-p", "no:cacheprovider",
         "--json-report", f"--json-report-file={out}"],
        cwd=str(WORKSPACE), env=env, check=False, capture_output=True, text=True,
        timeout=META.get("timeout", 300))
    if not out.is_file():
        return {}
    try:
        return json.loads(out.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return {}


def nice_name(nodeid: str) -> str:
    """'tests/suite_2_2_1/exercise_1/test_x.py::TestFoo::test_bar' -> 'TestFoo.test_bar'."""
    parts = nodeid.split("::")
    return ".".join(parts[1:]) if len(parts) > 1 else pathlib.Path(parts[0]).stem


def main() -> int:
    ensure_deps()
    report = run_pytest()
    points = int(META.get("points_per_test", 1))

    rows = []
    for t in report.get("tests") or []:
        passed = t.get("outcome") == "passed"
        rows.append({"test-name": nice_name(t.get("nodeid", "?")),
                     "passed": passed,
                     "score": points if passed else 0,
                     "max-score": points})

    if not rows:
        # Collection error, import failure, or a missing solution file: report one
        # explicit failing row rather than a silent 0/0 that looks like "no tests".
        rows = [{"test-name": "pytest collection", "passed": False,
                 "score": 0, "max-score": points}]

    result = {
        "schema": "classroom50/result/v1",
        "classroom": META["classroom"],
        "assignment": META["slug"],
        "tests": rows,
        "score": sum(r["score"] for r in rows),
        "max-score": sum(r["max-score"] for r in rows),
    }
    (WORKSPACE / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{result['score']}/{result['max-score']} across {len(rows)} test(s)")
    return 0        # a failing grade is not a runner failure


if __name__ == "__main__":
    raise SystemExit(main())
