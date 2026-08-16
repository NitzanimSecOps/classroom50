"""Tests for codo_sync.py — payload mapping + the scores.json walk (submit stubbed).
Run: python .github/scripts/test_codo_sync.py
"""
import json
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import codo_sync  # noqa: E402

# A real classroom50 result.json (the live demo grade), used to build fixtures.
RESULT = {
    "schema": "classroom50/result/v1", "classroom": "demo", "assignment": "smoke",
    "assignment_type": "individual", "owner": "opherul",
    "submission": "submit/2026-08-16T08-39-48Z-ec48f2d",
    "commit": "https://github.com/NitzanimSecOps/demo-smoke-opherul/commit/ec48f2dd66cecf6b77310d58c33c1bb3ff8139cc",
    "datetime": "2026-08-16T08:39:34Z", "score": 2, "max-score": 2,
    "tests": [{"test-name": "solution.py exists", "passed": True, "score": 1, "max-score": 1},
              {"test-name": "says hello", "passed": True, "score": 1, "max-score": 1}],
}


def test_to_payload_maps_result():
    p = codo_sync.to_payload(RESULT)
    assert p["github_login"] == "opherul" and p["slug"] == "smoke"
    assert p["release_tag"] == "submit/2026-08-16T08-39-48Z-ec48f2d"
    r = p["result"]
    assert r["graded_sha"] == "ec48f2dd66cecf6b77310d58c33c1bb3ff8139cc"
    assert r["committed_time"] == "2026-08-16T08:39:34Z"
    assert r["tests"] == [{"name": "solution.py exists", "passed": True, "score": 1},
                          {"name": "says hello", "passed": True, "score": 1}]


def test_sync_walks_scores_and_submits():
    calls = []
    orig = codo_sync.submit
    codo_sync.submit = lambda api, cid, payload, key: (calls.append((api, cid, payload, key)), (200, {"status": "ok"}))[1]
    os.environ["CODO_API_BASE"] = "https://backend.example"
    os.environ["CODO_COLLECTOR_KEY"] = "k"
    try:
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "demo").mkdir()
            sub = {k: v for k, v in RESULT.items() if k != "assignment"}  # bucket key is dropped
            scores = {"schema": "classroom50/scores/v1", "assignments": {
                "smoke": {"type": "individual", "entries": [
                    {"owner": "opherul", "submissions": [sub]}]}}}
            (pathlib.Path(d) / "demo" / "scores.json").write_text(json.dumps(scores))
            rc = codo_sync.main([d])
        assert rc == 0 and len(calls) == 1, calls
        api, cid, payload, key = calls[0]
        assert cid == "demo" and key == "k" and api == "https://backend.example"
        assert payload["github_login"] == "opherul" and payload["slug"] == "smoke"
        assert payload["result"]["committed_time"] == "2026-08-16T08:39:34Z"
    finally:
        codo_sync.submit = orig
        os.environ.pop("CODO_API_BASE", None)
        os.environ.pop("CODO_COLLECTOR_KEY", None)


def test_missing_config_fails():
    for k in ("CODO_API_BASE", "CODO_COLLECTOR_KEY"):
        os.environ.pop(k, None)
    assert codo_sync.main(["."]) == 1


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
