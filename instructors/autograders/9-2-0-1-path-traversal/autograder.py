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

Failed tests carry pytest's traceback (`--tb=short`) to the student on two surfaces:
the Actions log (one collapsible group per failure) and `release-body.md`, which the
runner publishes as the Release body and mirrors to the run's Summary page. The
traceback stays OUT of result.json (v1 rows are test-name/passed/score/max-score).
`short` shows only the failing line of each frame, not whole test functions, so the
hidden tests leak as little source as a useful trace allows.

Top-level student code: a student file that CALLS something at module level (a leftover
`print(f(x))`, `main()`, `name = input()`) runs the moment the hidden test imports it. On
the runner that can raise (no tty for os.getlogin, no stdin for input()), and the whole
exercise then scores 0 on one "pytest collection error" row. So when, and only when,
collection yields no tests, we grade AGAIN in the temp grading dir with the student's
top-level calls replaced by `pass` in place (line numbers stay true): first just bare
expression calls, then, if collection still fails, also any other top-level statement that
calls something. Imports, def and class always stay, and call-free constants stay. The
hidden tests are never altered, and the student checkout is never touched. A retried
submission scores normally, and a note (Release body + Actions log) names each skipped
line. Anything the retry can't fix (SyntaxError, missing name, bad test module...) falls
back to the ordinary collection-error row; a submission that collects is graded as before.

Timeout path: student code that never returns (an endless loop, a blocking accept()/recv())
used to kill the job with TimeoutExpired and publish nothing. Now pytest runs in its own
process group; at the deadline the WHOLE group is SIGKILLed, and we grade what finished.
pytest-json-report writes nothing until the session ends, so a tiny recorder plugin,
generated here at run time and loaded with `-p`, logs the collected node ids and every test
report to a file as they happen. Finished tests keep their real outcome; the test that was
running and every test that never ran fail with score 0. meta.json `timeout` limits the
first run; the first run plus the top-level-call retries share a budget of 2 x timeout (each
retry gets what is left). A note above the table (Release body + one Actions log line)
gives the timeout and the row that was running; the result is still published and we still
exit 0. If collection itself never finished and meta.json lists the tests, every listed row
fails.
"""
from __future__ import annotations

import ast
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.parse

BUNDLE = pathlib.Path(__file__).resolve().parent
WORKSPACE = pathlib.Path.cwd()
TESTS_DIR = BUNDLE / "tests"
META = json.loads((BUNDLE / "meta.json").read_text(encoding="utf-8"))

# Per-failure cap on the traceback we publish, so a runaway recursion or a huge repr
# can't bloat the release (mirrors runner.py's MAX_CAPTURED_CHARS).
MAX_TRACE_CHARS = 2000


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
    for f in [*WORKSPACE.glob("*.py"), *WORKSPACE.glob("*.sh")]:  # the student's flat code (repo root)
        shutil.copy2(f, d / f.name)
    for f in TESTS_DIR.glob("*.py"):        # the hidden tests shipped in the bundle
        shutil.copy2(f, d / f.name)
    return d


# The recorder plugin, written to its own temp dir at run time (the bundle is one file).
# It appends one JSON line per event, so the data survives a SIGKILL.
_RECORDER = """\
import json, os
_fd = None
def _w(rec):
    global _fd
    if _fd is None:
        _fd = os.open(%(path)r, os.O_WRONLY | os.O_APPEND | os.O_CREAT)
    os.write(_fd, (json.dumps(rec) + "\\n").encode("utf-8", "replace"))
def pytest_collection_finish(session):
    _w({"e": "collected", "ids": [i.nodeid for i in session.items]})
def pytest_runtest_logstart(nodeid, location):
    _w({"e": "start", "id": nodeid})
def pytest_runtest_logreport(report):
    text = report.longreprtext if report.outcome == "failed" else ""
    _w({"e": "report", "id": report.nodeid, "when": report.when,
        "outcome": report.outcome, "longrepr": text})
"""


def report_from_record(path: pathlib.Path) -> tuple[dict, str | None, bool]:
    """Rebuild a pytest-json-report-shaped dict from the recorder's log of a killed run.
    -> ({"tests": [...]} for every COLLECTED test, node id of the test that was running,
    whether the run stopped short: some test started but not every collected test
    finished). A test counts as finished only once its teardown was reported."""
    collected: list[str] = []
    started: list[str] = []
    phases: dict[str, dict] = {}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:      # a line cut off by the kill
            continue
        if rec.get("e") == "collected":
            collected = list(rec.get("ids") or [])
        elif rec.get("e") == "start":
            started.append(rec["id"])
        elif rec.get("e") == "report":
            phases.setdefault(rec["id"], {})[rec["when"]] = {
                "outcome": rec["outcome"], "longrepr": rec.get("longrepr", "")}
    tests, running, short = [], None, False
    for nid in collected:
        ph = phases.get(nid, {})
        if "teardown" in ph:
            if any(p["outcome"] == "failed" for p in ph.values()):
                outcome = "failed"
            elif ph.get("setup", {}).get("outcome") == "skipped":
                outcome = "skipped"
            else:
                outcome = ph.get("call", {}).get("outcome", "failed")
            tests.append({"nodeid": nid, "outcome": outcome, **ph})
            continue
        short = short or bool(started)
        if nid in started:
            running = nid
            msg = "This test was still running when the time limit was reached."
        else:
            msg = "This test did not run: the tests stopped before reaching it."
        tests.append({"nodeid": nid, "outcome": "failed",
                      "call": {"outcome": "failed", "longrepr": msg}})
    return {"tests": tests}, running, short


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)     # pytest leads its own process group
    except (ProcessLookupError, PermissionError):
        pass
    proc.kill()


def run_pytest(mutate=None, limit: float | None = None) -> tuple[dict, str, dict | None]:
    """Run pytest -> (parsed json report, combined stdout+stderr, hang). The output is
    returned (not discarded) so a collection/import error can be SURFACED rather than
    reduced to a cryptic 0/1. `hang` is None, or {"running": node id or None} when the
    run hit `limit` (default meta.json `timeout`): the process group was killed and the
    report is rebuilt from the recorder."""
    gdir = grading_dir()
    if mutate:
        mutate(gdir)
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="c50-pytest-"))
    out = tmp / "report.json"
    record = tmp / "record.jsonl"
    recdir = tmp / "rec"
    recdir.mkdir()
    (recdir / "c50_recorder.py").write_text(_RECORDER % {"path": str(record)}, encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(gdir), str(recdir)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if limit is None:
        limit = META.get("timeout", 300)
    proc = subprocess.Popen(
        [sys.executable, "-m", "pytest", ".", "-q", "--no-header", "--tb=short",
         "-p", "no:cacheprovider", "-p", "c50_recorder",
         "--json-report", f"--json-report-file={out}"],
        cwd=str(gdir), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, start_new_session=True)
    hang = None
    try:
        stdout, stderr = proc.communicate(timeout=limit)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:     # something outside the group holds the pipes
            stdout = stderr = ""
        report, running, _ = report_from_record(record)
        hang = {"running": running}
    output = (stdout or "") + (stderr or "")
    # A SyntaxError names the file by absolute path; show it as the flat name it is.
    output = output.replace(str(gdir) + os.sep, "")
    if hang:
        return report, output, hang
    # A short report: pytest ended (INTERNALERROR = exit 3, pytest.exit(returncode=0)) with
    # collected tests unfinished. The json report would list the interrupted test as
    # passed, so trust the recorder: only tests whose teardown was reported keep their outcome.
    rec_report, _, short = report_from_record(record)
    if rec_report["tests"] and (short or proc.returncode == 3):
        return rec_report, output, None
    if not out.is_file():
        return {}, output, None
    try:
        return json.loads(out.read_text(encoding="utf-8", errors="replace")), output, None
    except json.JSONDecodeError:
        return {}, output, None


def _top_level_skips(tree: ast.Module, wide: bool) -> list[ast.stmt]:
    """Module-level statements to neutralise. Narrow: a bare expression call (`print(f())`,
    `main()`). Wide: also any other statement that calls something (`x = input()`, a loop
    printing results), but never imports, def/class, docstrings, or an `if __name__`
    guard, which doesn't run on import anyway."""
    keep = (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    out = []
    for st in tree.body:
        if isinstance(st, keep):
            continue
        if isinstance(st, ast.Expr):
            if isinstance(st.value, ast.Call):
                out.append(st)
        elif wide and not (isinstance(st, ast.If) and any(
                isinstance(n, ast.Name) and n.id == "__name__" for n in ast.walk(st.test))
        ) and any(isinstance(n, ast.Call) for n in ast.walk(st)):
            out.append(st)
    return out


def _blank_out(src: bytes, stmts: list[ast.stmt]) -> bytes:
    """Replace each statement with `pass` on its first line and blank the rest of its
    lines, so every other line keeps its original number. Columns are UTF-8 byte offsets."""
    lines = src.split(b"\n")
    # right to left, so an earlier splice can't shift the columns of a later statement
    for st in sorted(stmts, key=lambda s: (s.lineno, s.col_offset), reverse=True):
        a, b = st.lineno - 1, st.end_lineno - 1
        head = lines[a][:st.col_offset]
        if a == b:
            lines[a] = head + b"pass" + lines[a][st.end_col_offset:]
            continue
        tail = lines[b][st.end_col_offset:]
        if tail.strip() and not tail.strip().startswith(b"#"):
            raise ValueError("code after a multi-line statement")
        lines[a] = head + b"pass" + (b"\r" if lines[a].endswith(b"\r") else b"")
        for i in range(a + 1, b + 1):
            lines[i] = b"\r" if lines[i].endswith(b"\r") else b""
    return b"\n".join(lines)


def skip_toplevel_calls(gdir: pathlib.Path, wide: bool) -> dict[str, list[int]]:
    """Rewrite the STUDENT files in the temp grading dir (never the hidden tests, never
    the checkout). -> {file name: original line numbers skipped}. A file that can't be
    parsed or rewritten is left as it is."""
    hidden = {f.name for f in TESTS_DIR.glob("*.py")}
    skipped: dict[str, list[int]] = {}
    for f in sorted(WORKSPACE.glob("*.py")):
        if f.name in hidden:
            continue
        try:
            src = (gdir / f.name).read_bytes().removeprefix(b"\xef\xbb\xbf")  # ast columns exclude the BOM
            stmts = _top_level_skips(ast.parse(src), wide)
            if not stmts:
                continue
            new = _blank_out(src, stmts)
            ast.parse(new)
        except Exception:
            continue
        (gdir / f.name).write_bytes(new)
        skipped[f.name] = sorted({n for st in stmts
                                  for n in range(st.lineno, st.end_lineno + 1)})
    return skipped


class _NothingNew(Exception):
    """The next retry would grade exactly what the last run already did."""


def retry_without_toplevel_calls(deadline: float) -> tuple[dict, str, dict[str, list[int]], dict | None] | None:
    """Collection found no tests: grade again with the student's top-level calls skipped,
    narrow then wide, each within what is left of the time budget (`deadline`, monotonic).
    -> (report, output, skipped, hang) from the first attempt that collects tests, or
    None (nothing to skip / didn't help / out of time / anything went wrong)."""
    previous: dict[str, list[int]] = {}
    for wide in (False, True):
        left = deadline - time.monotonic()
        if left < 1:
            return None
        skipped: dict[str, list[int]] = {}

        def mutate(gdir, wide=wide):
            skipped.update(skip_toplevel_calls(gdir, wide))
            if not skipped or skipped == previous:
                raise _NothingNew()
        try:
            report, output, hang = run_pytest(mutate, limit=left)
        except _NothingNew:
            continue
        except Exception as e:      # the retry must never cost the student a Release
            print(f"::warning::retry without top-level calls failed: "
                  f"{strip_control(str(e))[:200]}")
            return None
        if report.get("tests"):
            return report, output, skipped, hang
        previous = skipped
    return None


def skip_note(skipped: dict[str, list[int]]) -> str:
    """One sentence per file naming every skipped original line."""
    parts = []
    for name, nums in skipped.items():
        label = "Line" if len(nums) == 1 else "Lines"
        parts.append(f"{label} {', '.join(map(str, nums))} of {name} ran at import time "
                     "and was skipped for grading; put test code under "
                     '`if __name__ == "__main__":`')
    return "\n".join(parts)


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


def clip(text: str) -> str:
    """Cap a traceback at MAX_TRACE_CHARS, keeping the TAIL — pytest puts the actual
    error (`E   AssertionError: …`) last, which is the part a student needs."""
    text = text.rstrip()
    if len(text) > MAX_TRACE_CHARS:
        return "... (truncated)\n" + text[-MAX_TRACE_CHARS:]
    return text


_FRAME_RE = re.compile(r"^(\S.*?):(?:\d+|\?\?\?): in \S")


def trim_internal_frames(text: str) -> str:
    """Drop pytest/importlib frames from a collection error. The grading dir is flat, so
    the student's and the tests' own frames are bare names (`hello.py:3: in <module>`);
    a frame whose path has a separator or is `<frozen …>` is machinery. A frame's
    continuation lines are indented; anything at column 0 (E-lines, banners) is kept."""
    out, skip = [], False
    for line in text.splitlines():
        m = _FRAME_RE.match(line)
        if m:
            skip = bool(re.search(r"[\\/<]", m.group(1)))
        elif not line.startswith(" "):
            skip = False
        if not skip:
            out.append(line)
    return "\n".join(out)


def failure_trace(t: dict) -> str:
    """The traceback of a non-passing test from pytest-json-report: the longrepr of the
    phase that failed (a broken fixture fails in setup, not call), else the crash
    message, else just the outcome (skipped / xfailed / …)."""
    for phase in ("setup", "call", "teardown"):
        p = t.get(phase) or {}
        if p.get("outcome") != "failed":
            continue
        if p.get("longrepr"):
            return clip(str(p["longrepr"]))
        msg = (p.get("crash") or {}).get("message")
        if msg:
            return clip(str(msg))
    return f"pytest outcome: {t.get('outcome', '?')}"


def strip_control(s: str) -> str:
    """Drop control chars from a test name — it lands at column 0 of a log line, where
    a stray newline could start a workflow command."""
    return "".join(c for c in s if ord(c) >= 0x20 and ord(c) != 0x7f)


def fence(text: str) -> str:
    """A backtick fence longer than any backtick run in `text`, so student output
    containing ``` can't escape the code block and inject Markdown (runner.py _fence)."""
    longest = max((len(m.group(0)) for m in re.finditer(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def log_report(rows: list[dict], traces: list[str]) -> str:
    """PASS/FAIL per test, then one collapsible ::group:: per failure with its trace.
    Trace lines are indented two spaces: they carry student-controlled output, and
    GitHub only honours workflow commands (::error::, ::endgroup::) at column 0."""
    lines = [f"{'PASS' if r['passed'] else 'FAIL'}  {strip_control(r['test-name'])}  "
             f"({r['score']}/{r['max-score']})" for r in rows]
    for r, trace in zip(rows, traces):
        if r["passed"]:
            continue
        lines.append(f"::group::FAIL: {strip_control(r['test-name'])}")
        lines.extend(f"  {line}" for line in trace.splitlines())
        lines.append("::endgroup::")
    return "\n".join(lines) + "\n"


def release_body(rows: list[dict], traces: list[str], score: int, max_score: int,
                 note: str = "") -> str:
    """The Release body: score line, per-test table, and a collapsible section with the
    traceback of every failing test (same layout as runner.py render_declarative_body)."""
    lines = [f"### classroom50 autograde: {score}/{max_score}", ""]
    if note:
        lines += [f"> {n}  " for n in note.splitlines()] + [""]
    lines += ["| Test | Result | Score |", "|---|---|---|"]
    for r in rows:
        name = r["test-name"].replace("|", "\\|")
        lines.append(f"| {name} | {'PASS' if r['passed'] else 'FAIL'} | "
                     f"{r['score']} / {r['max-score']} |")
    lines.append("")
    failed = [(r, t) for r, t in zip(rows, traces) if not r["passed"]]
    if failed:
        lines += ["<details><summary>Failure details</summary>", ""]
        for r, trace in failed:
            f = fence(trace)
            lines += [f"**{r['test-name']}**", "", f, trace, f, ""]
        lines += ["</details>", ""]
    return "\n".join(lines)


def main() -> int:
    ensure_deps()
    timeout = META.get("timeout", 300)
    deadline = time.monotonic() + 2 * timeout      # first run + retries share this
    report, pytest_output, hang = run_pytest()
    points = int(META.get("points_per_test", 1))

    report_tests = report.get("tests") or []
    skip_text = ""
    if not report_tests:
        retried = retry_without_toplevel_calls(deadline)
        if retried:
            report, pytest_output, skipped, hang = retried
            report_tests = report["tests"]
            skip_text = skip_note(skipped)
            for line in skip_text.splitlines():
                print(f"::warning::{strip_control(line)}")
    # Names come from meta.json's `tests` — the SAME ordered list Codo is provisioned
    # with (docstring descriptions from collect_tests.py) — paired positionally with
    # pytest's collection order, so the feedback and Codo match exactly. Only trust the
    # pairing when the counts line up; otherwise fall back to per-test nice_name so a
    # drifted/partial collection can never mislabel a row.
    meta_tests = META.get("tests") or []
    if hang and not report_tests and meta_tests:
        # collection never finished: nothing was graded, so every listed row fails
        report_tests = [{"nodeid": "?", "outcome": "failed",
                         "call": {"outcome": "failed", "longrepr":
                                  "Collection did not finish: the tests stopped at import time."}}
                        for _ in meta_tests]
    use_meta = bool(meta_tests) and len(meta_tests) == len(report_tests)
    rows, traces = [], []    # traces[i] = row i's traceback ("" when it passed)
    for i, t in enumerate(report_tests):
        passed = t.get("outcome") == "passed"
        name = meta_tests[i] if use_meta else nice_name(t.get("nodeid", "?"))
        rows.append({"test-name": name,
                     "passed": passed,
                     "score": points if passed else 0,
                     "max-score": points})
        traces.append("" if passed else failure_trace(t))

    if hang:
        running = hang.get("running")
        where = ""
        for t, r in zip(report_tests, rows):
            if running and t.get("nodeid") == running:
                where = f" (while running: {strip_control(r['test-name'])})"
        n = int(timeout) if float(timeout).is_integer() else timeout
        hang_text = (f"The tests stopped after {n} s, probably an endless loop or a "
                     f"blocking call{where}.")
        print(f"::warning::{hang_text}")
        skip_text = "\n".join(x for x in (skip_text, hang_text) if x)

    if not rows:
        # No tests collected — a collection/import error (wrong or broken solution),
        # a missing solution file, or pytest failing to run. Surface the ACTUAL reason
        # instead of a bare 0/1: name the row with the specific error and carry pytest's
        # output as its trace, so a student sees WHY, not a cryptic "pytest collection".
        reason = f"timed out after {timeout} s" if hang else error_reason(pytest_output)
        print(f"::error::no tests collected — {reason}")
        rows = [{"test-name": f"pytest collection error: {reason}"[:200],
                 "passed": False, "score": 0, "max-score": points}]
        traces = [clip(trim_internal_frames(pytest_output)) or "(pytest produced no output)"]

    print(log_report(rows, traces), end="")

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
    # The runner only synthesizes a (trace-less) body when we don't write one.
    (WORKSPACE / "release-body.md").write_text(
        release_body(rows, traces, result["score"], result["max-score"], skip_text), encoding="utf-8")
    print(f"{result['score']}/{result['max-score']} across {len(rows)} test(s)")
    return 0        # a failing grade is not a runner failure


if __name__ == "__main__":
    raise SystemExit(main())
