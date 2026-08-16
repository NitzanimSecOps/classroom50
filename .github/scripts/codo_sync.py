#!/usr/bin/env python3
"""Codo-sync: push graded classroom50 submissions to the autograder backend.

Trusted control-plane step (a sibling to collect_scores.py). Reads each
`<classroom>/scores.json` (already produced by collect_scores.py) and POSTs every
submission to the backend's `/codo-submit`, which resolves the identity binding
(github_login -> codo_uid) and records the Codo submission. Idempotent on the backend
per (owner, slug, submission), so re-runs are safe; the onward Codo POST is the
backend's job. Never runs in a student repo — this holds the backend key.

classroom50 `result.json` v1 already carries owner/assignment/submission/datetime/
tests, so no git is needed here. Student `code` is left empty for now (grade + tests
are what Codo records); fetching per-submission code is a later refinement.

Env (set by codo-sync.yaml):
  CODO_API_BASE       backend base URL, e.g. https://gradfn....azurewebsites.net
  CODO_COLLECTOR_KEY  shared key the backend accepts as X-Collector-Key (Actions secret)
  CLASSROOM_FILTER    optional single-classroom limit
"""
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.request

_SHA_RE = re.compile(r"/commit/([0-9a-fA-F]{7,40})")


def graded_sha(result):
    m = _SHA_RE.search(result.get("commit") or "")
    if m:
        return m.group(1)
    sub = result.get("submission") or ""
    return sub.rsplit("-", 1)[-1] if "-" in sub else ""


def to_payload(result):
    tests = [{"name": t.get("test-name") or t.get("name") or "?",
              "passed": bool(t.get("passed")),
              "score": int(t.get("score", 0) or 0)}
             for t in (result.get("tests") or [])]
    return {"github_login": result["owner"], "slug": result["assignment"],
            "release_tag": result["submission"],
            "result": {"tests": tests, "code": "", "graded_sha": graded_sha(result),
                       "committed_time": result.get("datetime", "")}}


def iter_submissions(scores):
    for slug, bucket in (scores.get("assignments") or {}).items():
        for entry in (bucket.get("entries") or []):
            for sub in (entry.get("submissions") or []):
                r = dict(sub)
                r.setdefault("assignment", slug)
                yield r


def submit(api_base, class_id, payload, key):
    url = f"{api_base.rstrip('/')}/api/classes/{class_id}/codo-submit"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", "X-Collector-Key": key})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return getattr(r, "status", 200), json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "null")


def sync_classroom(root, classroom, api_base, key):
    path = pathlib.Path(root) / classroom / "scores.json"
    if not path.exists():
        print(f"::warning::{classroom}: no scores.json; skipping")
        return 0, 0
    scores = json.loads(path.read_text(encoding="utf-8"))
    ok = fail = 0
    for result in iter_submissions(scores):
        status, body = submit(api_base, classroom, to_payload(result), key)
        line = (f"{classroom}/{result.get('assignment')} {result.get('owner')} "
                f"{result.get('submission')} -> {status} {(body or {}).get('status')}")
        if status == 200:
            ok += 1
            print(f"  ok  {line}")
        else:
            fail += 1
            print(f"::warning::{line}")
    print(f"{classroom}: {ok} submitted, {fail} failed")
    return ok, fail


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    api_base = os.environ.get("CODO_API_BASE")
    key = os.environ.get("CODO_COLLECTOR_KEY")
    if not api_base or not key:
        print("::error::CODO_API_BASE and CODO_COLLECTOR_KEY are required")
        return 1
    root = pathlib.Path(argv[0]) if argv else pathlib.Path.cwd()
    only = (os.environ.get("CLASSROOM_FILTER") or "").strip()
    classrooms = [only] if only else sorted(p.parent.name for p in root.glob("*/scores.json"))
    total_ok = total_fail = 0
    for c in classrooms:
        ok, fail = sync_classroom(root, c, api_base, key)
        total_ok += ok
        total_fail += fail
    print(f"\ncodo-sync: {total_ok} submitted, {total_fail} failed "
          f"across {len(classrooms)} classroom(s)")
    return 1 if total_fail else 0


if __name__ == "__main__":
    sys.exit(main())
