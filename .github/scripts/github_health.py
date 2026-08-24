"""Is GitHub actually healthy, or is that failure theirs? Answer in one line.

Motivation. On 2026-08-17 a grade job died on a codeload 429 and the selective
failure pattern (some endpoints 200, others 503) was misread as *our* egress problem;
~90 min went into a NAT-gateway fix before GitHub's status page confirmed a partial
incident. This helper turns that question into a single call to GitHub's public status
API (no auth, no deps beyond the stdlib), so it runs anywhere - a laptop, CI, a runner.
codo_sync calls it automatically when a sweep hard-fails, to attribute the failure.

    python .github/scripts/github_health.py            # human-readable, exit 0/2
    python .github/scripts/github_health.py --quiet     # one line + exit code, for gating

Exit codes:
    0  indicator == "none" AND no unresolved incidents      (all clear)
    2  any degradation / outage / unresolved incident        (suspect GitHub, not us)
    3  could not reach the status API                         (inconclusive)

⚠ A green result is necessary, not sufficient: the status page LAGS reality. So a RED
result is decisive ("it's them, stop debugging us"); a GREEN result only means "no
*confirmed* incident yet" - re-check in a few minutes if symptoms persist.
"""
import json
import sys
import urllib.request

SUMMARY_URL = "https://www.githubstatus.com/api/v2/summary.json"

# Components the autograder pipeline actually depends on, mapped to the layer they break.
RELEVANT = {
    "Git Operations": "source (student push; the repo IS the submission)",
    "Actions":        "CI orchestration (schedules the grade job)",
    "API Requests":   "codo_sync (reads Releases) + gh student accept",
    "Pages":          "classroom50 serves assignments.json + bundles via Pages",
    "Webhooks":       "push -> Actions trigger; release -> backend sync",
}


def fetch(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": "autograder-health/1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def main(argv):
    quiet = "--quiet" in argv
    try:
        data = fetch(SUMMARY_URL)
    except Exception as e:  # network, DNS, timeout, non-200 - all inconclusive
        print(f"github-health: COULD NOT REACH status API ({e})", file=sys.stderr)
        return 3

    status = data.get("status", {})
    indicator = status.get("indicator", "unknown")   # none | minor | major | critical
    description = status.get("description", "")
    incidents = [i for i in data.get("incidents", []) if not i.get("resolved_at")]

    bad_components = [
        c for c in data.get("components", [])
        if c.get("name") in RELEVANT and c.get("status") != "operational"
    ]

    healthy = indicator == "none" and not incidents

    if quiet:
        summary = "OK" if healthy else f"INCIDENT ({indicator}: {description})"
        print(f"github-health: {summary}")
        return 0 if healthy else 2

    print(f"GitHub status: {indicator.upper()} - {description}\n")

    if bad_components:
        print("Autograder-relevant components NOT operational:")
        for c in bad_components:
            print(f"  [{c['status']:<18}] {c['name']:<15} -> {RELEVANT[c['name']]}")
    else:
        print("All autograder-relevant components report operational.")

    if incidents:
        print(f"\nUnresolved incident(s): {len(incidents)}")
        for i in incidents:
            updates = i.get("incident_updates", [])
            latest = updates[0].get("body", "").strip() if updates else ""
            print(f"  - {i.get('name')}  [impact={i.get('impact')}, "
                  f"status={i.get('status')}, since {i.get('created_at')}]")
            if latest:
                print(f"    latest: {latest[:200]}")

    if healthy:
        print("\nVERDICT: no confirmed incident. NB the status page LAGS - re-check if "
              "symptoms persist.")
    else:
        print("\nVERDICT: GitHub is degraded. A pipeline failure right now is most likely "
              "THEIRS, not ours.")
    return 0 if healthy else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
