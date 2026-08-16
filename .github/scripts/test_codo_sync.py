"""Tests for codo_sync.py — result.json -> payload, and the team+release walk
(GitHub + backend stubbed). Run: python .github/scripts/test_codo_sync.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import codo_sync  # noqa: E402

RESULT = {  # a real classroom50 result.json (the live demo grade)
    "schema": "classroom50/result/v1", "classroom": "demo", "assignment": "smoke",
    "owner": "opherul", "submission": "submit/2026-08-16T08-39-48Z-ec48f2d",
    "commit": "https://github.com/NitzanimSecOps/demo-smoke-opherul/commit/ec48f2dd66cecf6b77310d58c33c1bb3ff8139cc",
    "datetime": "2026-08-16T08:39:34Z", "score": 2, "max-score": 2,
    "tests": [{"test-name": "solution.py exists", "passed": True, "score": 1},
              {"test-name": "says hello", "passed": True, "score": 1}],
}


def test_to_payload_maps_result():
    p = codo_sync.to_payload(RESULT)
    assert p["github_login"] == "opherul" and p["slug"] == "smoke"
    assert p["release_tag"] == "submit/2026-08-16T08-39-48Z-ec48f2d"
    assert p["result"]["graded_sha"] == "ec48f2dd66cecf6b77310d58c33c1bb3ff8139cc"
    assert p["result"]["committed_time"] == "2026-08-16T08:39:34Z"
    assert p["result"]["tests"] == [{"name": "solution.py exists", "passed": True, "score": 1},
                                    {"name": "says hello", "passed": True, "score": 1}]


def test_sync_classroom_walks_team_and_releases():
    calls = []
    saved = (codo_sync.team_members, codo_sync.repo_results,
             codo_sync.assignment_slugs, codo_sync.submit)
    codo_sync.team_members = lambda org, classroom, token: ["opherul"]
    codo_sync.assignment_slugs = lambda root, classroom: ["smoke"]
    codo_sync.repo_results = lambda org, repo, token: (iter([RESULT]) if repo == "demo-smoke-opherul" else iter([]))
    codo_sync.submit = lambda api, cid, payload, key: (calls.append((cid, payload)), (200, {"status": "ok"}))[1]
    try:
        ok, fail = codo_sync.sync_classroom(".", "NitzanimSecOps", "demo", "https://b", "k", "tok")
        assert ok == 1 and fail == 0 and len(calls) == 1, (ok, fail, calls)
        cid, payload = calls[0]
        assert cid == "demo" and payload["github_login"] == "opherul" and payload["slug"] == "smoke"
    finally:
        (codo_sync.team_members, codo_sync.repo_results,
         codo_sync.assignment_slugs, codo_sync.submit) = saved


def test_dry_run_skips_submit():
    saved = (codo_sync.team_members, codo_sync.repo_results, codo_sync.assignment_slugs)
    codo_sync.team_members = lambda org, classroom, token: ["opherul"]
    codo_sync.assignment_slugs = lambda root, classroom: ["smoke"]
    codo_sync.repo_results = lambda org, repo, token: iter([RESULT])
    hit = []
    codo_sync.submit = lambda *a, **k: hit.append(1)  # must NOT be called
    try:
        ok, fail = codo_sync.sync_classroom(".", "org", "demo", None, None, "tok", dry_run=True)
        assert ok == 1 and fail == 0 and hit == []
    finally:
        (codo_sync.team_members, codo_sync.repo_results, codo_sync.assignment_slugs) = saved


def test_group_credits_owner_and_rostered_collaborators():
    group = {**RESULT, "assignment": "groupex", "assignment_type": "group", "owner": "founder",
             "submission": "submit/2026-08-16T10-00-00Z-abc1234"}
    saved = (codo_sync.team_members, codo_sync.repo_results, codo_sync.assignment_slugs,
             codo_sync.repo_collaborators, codo_sync.submit)
    codo_sync.team_members = lambda o, c, t: ["founder", "alice", "bob"]          # the roster
    codo_sync.assignment_slugs = lambda r, c: ["groupex"]
    codo_sync.repo_results = lambda o, repo, t: (iter([group]) if repo == "demo-groupex-founder" else iter([]))
    codo_sync.repo_collaborators = lambda o, repo, t: ["founder", "alice", "charlie"]  # charlie: not rostered
    submitted = []
    codo_sync.submit = lambda api, cid, p, key: (submitted.append(p["github_login"]), (200, {"status": "ok"}))[1]
    try:
        ok, fail = codo_sync.sync_classroom(".", "NitzanimSecOps", "demo", "https://b", "k", "tok")
        # founder (owner) + alice (rostered collaborator); NOT charlie (not rostered),
        # NOT bob (rostered but not a collaborator on this repo).
        assert ok == 2 and fail == 0, (ok, fail, submitted)
        assert set(submitted) == {"founder", "alice"}, submitted
    finally:
        (codo_sync.team_members, codo_sync.repo_results, codo_sync.assignment_slugs,
         codo_sync.repo_collaborators, codo_sync.submit) = saved


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  ok   {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {t.__name__}: {e or 'assertion failed'}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ERR  {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
