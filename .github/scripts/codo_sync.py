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
import urllib.error
import urllib.request

GH_API = os.environ.get("GITHUB_API_URL", "https://api.github.com")
_SHA_RE = re.compile(r"/commit/([0-9a-fA-F]{7,40})")


# ---- GitHub reads (secret team + student repo releases) ----------------------
def _headers(token, accept="application/vnd.github+json"):
    return {"Authorization": f"Bearer {token}", "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "classroom50-codo-sync"}


def _gh(path, token, accept="application/vnd.github+json"):
    req = urllib.request.Request(GH_API + path, headers=_headers(token, accept))
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def _gh_paged(path, token):
    """GET a paginated list endpoint, following `Link: rel=next`. Returns all items,
    so large rosters / release histories aren't silently truncated at 100."""
    sep = "&" if "?" in path else "?"
    url = GH_API + path + f"{sep}per_page=100"
    items = []
    while url:
        req = urllib.request.Request(url, headers=_headers(token))
        with urllib.request.urlopen(req, timeout=30) as r:
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


def to_payload(result, login=None):
    """`login` overrides the credited student (group members); defaults to owner."""
    tests = [{"name": t.get("test-name") or t.get("name") or "?",
              "passed": bool(t.get("passed")),
              "score": int(t.get("score", 0) or 0)}
             for t in (result.get("tests") or [])]
    return {"github_login": login or result["owner"], "slug": result["assignment"],
            "release_tag": result["submission"],
            "result": {"tests": tests, "code": "", "graded_sha": graded_sha(result),
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


# ---- orchestration ----------------------------------------------------------
def sync_classroom(root, org, classroom, api_base, key, token, dry_run=False):
    roster_names = team_members(org, classroom, token)   # the secret team = the roster
    roster = {m.lower() for m in roster_names}
    slugs = assignment_slugs(root, classroom)
    ok = fail = 0
    # Iterate the roster × assignments. A group assignment's repo lives under the
    # founder only, so a teammate's own repo 404s (skipped) — they're instead credited
    # when we process the founder's repo, via credited_logins (owner + rostered collabs).
    for user in roster_names:
        for slug in slugs:
            repo = f"{classroom}-{slug}-{user}"
            for result in repo_results(org, repo, token):
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
    print(f"{classroom}: {ok} submitted, {fail} failed "
          f"({len(roster_names)} students x {len(slugs)} assignments)")
    return ok, fail


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    org = os.environ.get("GITHUB_REPOSITORY_OWNER") or os.environ.get("ORG")
    api_base = os.environ.get("CODO_API_BASE")
    key = os.environ.get("CODO_COLLECTOR_KEY")
    token = os.environ.get("GH_TOKEN")
    dry = os.environ.get("DRY_RUN") == "1"
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
    for c in classrooms:
        ok, fail = sync_classroom(root, org, c, api_base, key, token, dry_run=dry)
        total_ok += ok
        total_fail += fail
    print(f"\ncodo-sync: {total_ok} submitted, {total_fail} failed "
          f"across {len(classrooms)} classroom(s)")
    return 1 if total_fail else 0


if __name__ == "__main__":
    sys.exit(main())
