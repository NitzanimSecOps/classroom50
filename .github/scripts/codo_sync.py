#!/usr/bin/env python3
"""Codo-sync: push graded classroom50 submissions to the autograder backend.

Plan B — team + Release driven, so NO roster.csv / scores.json ever land in the
public config repo. The roster is the classroom's **secret GitHub team**
(`classroom50-<classroom>`, private membership); grades live in Codo, so we read
each graded `result.json` straight from the student repos' `submit/*` **Releases**
(no `collect_scores`, no `scores.json`). For each result we POST to the backend's
`/codo-submit` (X-Collector-Key), which resolves the identity binding and records the
Codo submission. Idempotent on the backend per (owner, slug, submission).

Env (set by codo-sync.yaml):
  CODO_API_BASE           backend base URL, e.g. https://gradfn....azurewebsites.net
  CODO_COLLECTOR_KEY      shared key the backend accepts as X-Collector-Key (secret)
  GH_TOKEN                PAT that can read the secret team + student repo releases
                          (reuse CLASSROOM50_SERVICE_TOKEN)
  CLASSROOM_FILTER        optional single-classroom limit
  DRY_RUN=1               walk + print payloads, skip the POST
"""
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

GH_API = os.environ.get("GITHUB_API_URL", "https://api.github.com")
_SHA_RE = re.compile(r"/commit/([0-9a-fA-F]{7,40})")

# Transient GitHub failures worth retrying: rate-limit + the 5xx family. A partial
# GitHub incident (e.g. the 2026-08 runner-group one) shouldn't abort a whole sweep
# mid-roster — retry with backoff, then let the error surface so the NEXT sweep re-reads.
_RETRY_CODES = {429, 500, 502, 503, 504}


def _urlopen_retry(req, timeout=30, tries=4, base=1.5):
    """urlopen with exponential backoff on transient failures (429/5xx + URLError).
    Non-transient HTTPError (404/403/…) raises immediately so callers keep their
    existing handling; the last attempt re-raises whatever failed."""
    for i in range(tries):
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code not in _RETRY_CODES or i == tries - 1:
                raise
            print(f"::warning::GitHub {e.code} on {req.full_url} — retry {i + 1}/{tries - 1}")
        except urllib.error.URLError as e:
            if i == tries - 1:
                raise
            print(f"::warning::GitHub unreachable ({e.reason}) — retry {i + 1}/{tries - 1}")
        time.sleep(base * (2 ** i))


# ---- GitHub reads (secret team + student repo releases) ----------------------
def _headers(token, accept="application/vnd.github+json"):
    return {"Authorization": f"Bearer {token}", "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "classroom50-codo-sync"}


def _gh(path, token, accept="application/vnd.github+json"):
    req = urllib.request.Request(GH_API + path, headers=_headers(token, accept))
    with _urlopen_retry(req, timeout=30) as r:
        return r.read()


def _gh_paged(path, token):
    """GET a paginated list endpoint, following `Link: rel=next`. Returns all items,
    so large rosters / release histories aren't silently truncated at 100."""
    sep = "&" if "?" in path else "?"
    url = GH_API + path + f"{sep}per_page=100"
    items = []
    while url:
        req = urllib.request.Request(url, headers=_headers(token))
        with _urlopen_retry(req, timeout=30) as r:
            items.extend(json.loads(r.read().decode()))
            link = r.headers.get("Link", "") or ""
        url = None
        for part in link.split(","):
            if 'rel="next"' in part and "<" in part and ">" in part:
                url = part[part.find("<") + 1:part.find(">")]
    return items


def team_members(org, classroom, token):
    try:
        return [m["login"] for m in _gh_paged(f"/orgs/{org}/teams/classroom50-{classroom}/members", token)]
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"::warning::no secret team classroom50-{classroom}; skipping")
            return []
        if e.code == 403:
            print(f"::error::403 reading team classroom50-{classroom} members — the token "
                  f"(CLASSROOM50_SERVICE_TOKEN) needs org 'Members: read' (fine-grained PAT) "
                  f"or 'read:org' (classic). It reads the secret team as the Plan-B roster.")
        raise


def repo_collaborators(org, repo, token):
    return [c["login"] for c in _gh_paged(f"/repos/{org}/{repo}/collaborators", token)]


def credited_logins(org, repo, result, roster, token):
    """Who to credit for this result. Individual → the owner. Group → the owner PLUS
    every repo collaborator on the roster (case-insensitive), matching classroom50's
    collect_scores: crediting is gated on roster membership, not collaborator
    permission; a non-rostered collaborator (staff/org-owner) is never credited. On a
    collaborator-read failure, degrade to owner-only. `roster` is a lowercased set."""
    owner = result.get("owner", "")
    if (result.get("assignment_type") or "").lower() != "group":
        return [owner] if owner else []
    try:
        collabs = repo_collaborators(org, repo, token)
    except urllib.error.HTTPError:
        print(f"::warning::{repo}: can't read collaborators; crediting owner only")
        return [owner]
    out, seen, ownerk = [], set(), owner.lower()
    for login in [owner, *collabs]:
        k = login.lower()
        if k != ownerk and k not in roster:
            continue
        if k not in seen:
            seen.add(k)
            out.append(login)
    return out


def repo_exists(org, repo, token):
    """True if the student repo exists (i.e. the student accepted). Used by the
    reconciler to tell 'never accepted' (no repo) from 'accepted but never graded'
    (repo exists, no submit/* release) — the latter is an invisible gap worth flagging."""
    req = urllib.request.Request(f"{GH_API}/repos/{org}/{repo}", headers=_headers(token))
    try:
        with _urlopen_retry(req, timeout=30):
            return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise


def repo_results(org, repo, token):
    """Yield each result.json (dict) from `repo`'s submit/* releases. A 404 means
    the student never accepted/submitted — not an error."""
    try:
        releases = _gh_paged(f"/repos/{org}/{repo}/releases", token)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return
        raise
    for rel in releases:
        if not str(rel.get("tag_name", "")).startswith("submit/"):
            continue
        for asset in rel.get("assets", []):
            if asset.get("name") == "result.json":
                raw = _gh(f"/repos/{org}/{repo}/releases/assets/{asset['id']}", token,
                          accept="application/octet-stream")
                try:
                    yield json.loads(raw.decode())
                except Exception:  # noqa: BLE001
                    print(f"::warning::{repo}: unparseable result.json in {rel.get('tag_name')}")


def assignment_slugs(root, classroom):
    path = pathlib.Path(root) / classroom / "assignments.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [a["slug"] for a in (data.get("assignments") or []) if isinstance(a, dict) and a.get("slug")]


# ---- result.json -> /codo-submit body ---------------------------------------
def graded_sha(result):
    m = _SHA_RE.search(result.get("commit") or "")
    if m:
        return m.group(1)
    sub = result.get("submission") or ""
    return sub.rsplit("-", 1)[-1] if "-" in sub else ""


def submission_code(result):
    """The `code` blob Codo stores for the submission. Codo keeps `code` as a
    {filename: contents} object (an empty string 500s the json column), and its editor
    shows those files to a teacher opening the submission. The GitHub repo IS the real
    submission, so instead of null we put a single pointer file there — giving the
    teacher a click-through to the repo/commit right from the Codo submission view.
    Returns None if we can't derive the repo (then the backend sends null, as before)."""
    repo = (result.get("commit") or "").split("/commit/")[0]
    if not repo:
        return None
    tests = result.get("tests") or []
    passed = sum(1 for t in tests if t.get("passed"))
    body = (
        "# This exercise is graded on GitHub - the repository IS the submission.\n"
        f"# repo:    {repo}\n"
        f"# commit:  {graded_sha(result)}\n"
        f"# release: {result.get('release', '')}\n"
        f"# result:  {passed}/{len(tests)} tests passed "
        "(full log in the repo's Actions run)\n"
    )
    return {"submission.py": body}


def to_payload(result, login=None):
    """`login` overrides the credited student (group members); defaults to owner."""
    tests = [{"name": t.get("test-name") or t.get("name") or "?",
              "passed": bool(t.get("passed")),
              "score": int(t.get("score", 0) or 0)}
             for t in (result.get("tests") or [])]
    return {"github_login": login or result["owner"], "slug": result["assignment"],
            "release_tag": result["submission"],
            "result": {"tests": tests, "code": submission_code(result),
                       "graded_sha": graded_sha(result),
                       "committed_time": result.get("datetime", "")}}


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
    except urllib.error.URLError as e:
        # Backend unreachable (down / restarting / DNS). Report as a failed attempt
        # (status 0) so the roster sweep records it and CONTINUES — a single dead
        # backend must never abort the whole run. The Release persists; the next
        # sweep resends (the backend skips only already-'ok' submissions).
        return 0, {"error": f"backend unreachable: {e.reason}"}


# ---- orchestration ----------------------------------------------------------
def sync_classroom(root, org, classroom, api_base, key, token, dry_run=False, reconcile=False):
    roster_names = team_members(org, classroom, token)   # the secret team = the roster
    roster = {m.lower() for m in roster_names}
    slugs = assignment_slugs(root, classroom)
    ok = fail = gaps = 0
    # Iterate the roster × assignments. A group assignment's repo lives under the
    # founder only, so a teammate's own repo 404s (skipped) — they're instead credited
    # when we process the founder's repo, via credited_logins (owner + rostered collabs).
    for user in roster_names:
        for slug in slugs:
            repo = f"{classroom}-{slug}-{user}"
            saw_result = False
            for result in repo_results(org, repo, token):
                saw_result = True
                for login in credited_logins(org, repo, result, roster, token):
                    payload = to_payload(result, login=login)
                    tag = payload["release_tag"]
                    if dry_run:
                        print(f"  DRY {classroom}/{slug} {login} {tag}")
                        ok += 1
                        continue
                    status, body = submit(api_base, classroom, payload, key)
                    line = f"{classroom}/{slug} {login} {tag} -> {status} {(body or {}).get('status')}"
                    if status == 200:
                        ok += 1
                        print(f"  ok  {line}")
                    else:
                        fail += 1
                        print(f"::warning::{line}")
            # Reconcile: a repo that exists but produced no submit/* release is an
            # accepted-but-never-graded gap (a push that never triggered/finished the
            # grade job). Invisible otherwise — surface it so a human can regrade.
            if reconcile and not saw_result and repo_exists(org, repo, token):
                gaps += 1
                print(f"::warning::GAP {classroom}/{slug} {user}: repo exists, no submit/* "
                      f"release (accepted but never graded)")
    tail = f", {gaps} ungraded gap(s)" if reconcile else ""
    print(f"{classroom}: {ok} submitted, {fail} failed{tail} "
          f"({len(roster_names)} students x {len(slugs)} assignments)")
    return ok, fail


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    org = os.environ.get("GITHUB_REPOSITORY_OWNER") or os.environ.get("ORG")
    api_base = os.environ.get("CODO_API_BASE")
    key = os.environ.get("CODO_COLLECTOR_KEY")
    token = os.environ.get("GH_TOKEN")
    dry = os.environ.get("DRY_RUN") == "1"
    reconcile = os.environ.get("RECONCILE") == "1"
    need = [k for k, v in [("ORG/GITHUB_REPOSITORY_OWNER", org), ("GH_TOKEN", token),
                           ("CODO_API_BASE", api_base if not dry else "x"),
                           ("CODO_COLLECTOR_KEY", key if not dry else "x")] if not v]
    if need:
        print(f"::error::missing required env: {', '.join(need)}")
        return 1
    root = pathlib.Path(argv[0]) if argv else pathlib.Path.cwd()
    only = (os.environ.get("CLASSROOM_FILTER") or "").strip()
    classrooms = [only] if only else sorted(p.parent.name for p in root.glob("*/assignments.json"))
    total_ok = total_fail = 0
    hard_fail = 0
    for c in classrooms:
        # Isolate classrooms: a hard failure in one (e.g. a team read that 5xxs past
        # all retries) is reported and skipped, never allowed to abort the rest.
        try:
            ok, fail = sync_classroom(root, org, c, api_base, key, token,
                                      dry_run=dry, reconcile=reconcile)
        except Exception as e:  # noqa: BLE001
            hard_fail += 1
            print(f"::error::classroom {c!r} sync aborted: {type(e).__name__}: {e}")
            continue
        total_ok += ok
        total_fail += fail
    print(f"\ncodo-sync: {total_ok} submitted, {total_fail} failed "
          f"across {len(classrooms)} classroom(s)"
          + (f"; {hard_fail} classroom(s) aborted" if hard_fail else ""))
    # On a hard failure, attribute it: was GitHub degraded, or is it us? (2026-08-17
    # lesson — a partial GitHub incident was misread as our egress problem for ~90 min.)
    if hard_fail:
        try:
            import github_health
            github_health.main(["--quiet"])
        except Exception:  # noqa: BLE001 - diagnostics must never mask the real failure
            pass
    return 1 if (total_fail or hard_fail) else 0


if __name__ == "__main__":
    sys.exit(main())
