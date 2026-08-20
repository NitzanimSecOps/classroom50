#!/usr/bin/env python3
"""classroom50 bundle entrypoint — grade a pytest exercise. Generic: identical for
every exercise, generated into each assignment bundle by apply_classroom50.py.

How the runner invokes us (runner.py run_entrypoint / finalize_result):
  * `python <bundle>/autograder.py` with **cwd = the student checkout**
  * we must write a COMPLETE v1 `result.json` into cwd and exit 0
  * the runner stamps ONLY `owner` / `assignment_type` / `datetime` / `graded_at` /
    `submitted_by` afterwards (those are runner-authoritative). Everything else it
    *validates* rather than authors — so we MUST write `submission` / `commit` /
    `release` / `review`, or finalize_result rejects the result ("'submission' must be
    a 'submit/*' string") and no Release is published. We build them from the env the
    runner passes (SUBMISSION_TAG, GITHUB_REPOSITORY, GITHUB_SHA, GITHUB_SERVER_URL),
    matching runner.py's commit_url / release_url formats exactly.

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
import shutil
import subprocess
import sys
import tempfile
import urllib.parse

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


def grading_dir() -> pathlib.Path:
    """A temp dir holding the student's FLAT solution file(s) + the bundled tests, so
    pytest runs them as flat siblings — `from solution import …` / `from warehouse import
    …` (tests) and the solution's own `from warehouse_setup import …` all resolve. Flat
    model: nothing to reconstruct, no package. The student repo is flat (files at root =
    the workspace), so we copy the workspace's root .py + the hidden bundle tests together."""
    d = pathlib.Path(tempfile.mkdtemp(prefix="c50-grade-"))
    for f in WORKSPACE.glob("*.py"):        # the student's flat code (repo root)
        shutil.copy2(f, d / f.name)
    for f in TESTS_DIR.glob("*.py"):        # the hidden tests shipped in the bundle
        shutil.copy2(f, d / f.name)
    return d


def run_pytest() -> tuple[dict, str]:
    """Run pytest → (parsed json report, combined stdout+stderr). The output is
    returned (not discarded) so a collection/import error can be SURFACED rather than
    reduced to a cryptic 0/1."""
    gdir = grading_dir()
    out = pathlib.Path(tempfile.mkdtemp(prefix="c50-pytest-")) / "report.json"
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(gdir)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", ".", "-q", "--no-header",
         "-p", "no:cacheprovider",
         "--json-report", f"--json-report-file={out}"],
        cwd=str(gdir), env=env, check=False, capture_output=True, text=True,
        timeout=META.get("timeout", 300))
    output = (proc.stdout or "") + (proc.stderr or "")
    if not out.is_file():
        return {}, output
    try:
        return json.loads(out.read_text(encoding="utf-8", errors="replace")), output
    except json.JSONDecodeError:
        return {}, output


def nice_name(nodeid: str) -> str:
    """'tests/suite_2_2_1/exercise_1/test_x.py::TestFoo::test_bar' -> 'TestFoo.test_bar'."""
    parts = nodeid.split("::")
    return ".".join(parts[1:]) if len(parts) > 1 else pathlib.Path(parts[0]).stem


def error_reason(output: str) -> str:
    """Pull the most useful one-liner out of pytest's output for a no-tests-collected
    run — usually an import/collection error like a wrong or broken solution file."""
    lines = output.splitlines()
    # The common "student pushed something broken" causes.
    exc = ("ImportError", "ModuleNotFoundError", "SyntaxError", "IndentationError",
           "NameError", "AttributeError", "TypeError", "ValueError")
    # Prefer pytest's 'E   ' error-DETAIL lines (the precise cause, e.g.
    # 'E   ImportError: cannot import name X') over its prose header.
    for line in lines:
        s = line.strip()
        if s.startswith("E ") and not s.startswith("E   assert"):
            s = s[1:].strip()
            if s.startswith(exc):
                return s[:180]
    # Then any exception-typed line, skipping pytest's noisy path header.
    for line in lines:
        s = line.strip()
        if s.startswith(exc) and "while importing" not in s:
            return s[:180]
    for line in lines:
        if "Error" in line and "::" not in line and "while importing" not in line:
            return line.strip()[:180]
    return "no tests collected"


def main() -> int:
    ensure_deps()
    report, pytest_output = run_pytest()
    points = int(META.get("points_per_test", 1))

    report_tests = report.get("tests") or []
    # Names come from meta.json's `tests` — the SAME ordered list Codo is provisioned
    # with (docstring descriptions from collect_tests.py) — paired positionally with
    # pytest's collection order, so the feedback and Codo match exactly. Only trust the
    # pairing when the counts line up; otherwise fall back to per-test nice_name so a
    # drifted/partial collection can never mislabel a row.
    meta_tests = META.get("tests") or []
    use_meta = bool(meta_tests) and len(meta_tests) == len(report_tests)
    rows = []
    for i, t in enumerate(report_tests):
        passed = t.get("outcome") == "passed"
        name = meta_tests[i] if use_meta else nice_name(t.get("nodeid", "?"))
        rows.append({"test-name": name,
                     "passed": passed,
                     "score": points if passed else 0,
                     "max-score": points})

    if not rows:
        # No tests collected — a collection/import error (wrong or broken solution),
        # a missing solution file, or pytest failing to run. Surface the ACTUAL reason
        # instead of a bare 0/1: echo pytest's output to the job log and name the row
        # with the specific error, so a student sees WHY, not a cryptic "pytest collection".
        reason = error_reason(pytest_output)
        print("::group::pytest produced no tests — full output")
        print(pytest_output.strip()[-4000:] or "(pytest produced no output)")
        print("::endgroup::")
        print(f"::error::no tests collected — {reason}")
        rows = [{"test-name": f"pytest collection error: {reason}"[:200],
                 "passed": False, "score": 0, "max-score": points}]

    # Runner-authoritative fields (owner/assignment_type/datetime/graded_at/
    # submitted_by) are stamped by finalize_result. But submission/commit/release/
    # review are only VALIDATED, not authored, so we must set them from the runner's
    # env. Formats mirror runner.py:commit_url / release_url; `review` falls back to
    # the commit view (validate_result only needs a non-empty string).
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    sha = os.environ.get("GITHUB_SHA", "")
    submission = os.environ.get("SUBMISSION_TAG", "")
    commit_url = f"{server}/{repo}/commit/{sha}"
    release_url = f"{server}/{repo}/releases/tag/{urllib.parse.quote(submission, safe='')}"

    result = {
        "schema": "classroom50/result/v1",
        "classroom": META["classroom"],
        "assignment": META["slug"],
        "submission": submission,
        "commit": commit_url,
        "release": release_url,
        "review": commit_url,
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
