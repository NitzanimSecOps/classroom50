#!/usr/bin/env bash
# Team-direct enrollment (Plan B privacy).
#
# Adds a student to the org + the classroom's SECRET team WITHOUT writing any PII
# into <classroom>/roster.csv — unlike `gh teacher roster add`, which commits the
# student's name/email/etc. into the PUBLIC config repo. The secret team's membership
# is private (visible only to org owners), so it is the roster; codo_sync reads it via
# the API. The student can then `gh student accept` as usual.
#
# Requires org-owner auth (`gh teacher login`, or `gh auth login` as an owner).
# Usage: enroll_student.sh <org> <classroom> <github-username>
set -euo pipefail

if [ "$#" -ne 3 ]; then
  echo "usage: $0 <org> <classroom> <github-username>" >&2
  exit 2
fi
ORG="$1"; CLASSROOM="$2"; USER="$3"
TEAM="classroom50-${CLASSROOM}"

# 1) ensure org membership (pending invite until the student accepts it)
gh api --method PUT "/orgs/${ORG}/memberships/${USER}" -f role=member >/dev/null

# 2) add to the classroom's secret team (this is the private roster)
gh api --method PUT "/orgs/${ORG}/teams/${TEAM}/memberships/${USER}" -f role=member >/dev/null

echo "enrolled ${USER} in ${ORG}/${CLASSROOM} via team ${TEAM} — roster.csv untouched."
echo "the student runs: gh student accept ${ORG} ${CLASSROOM} <assignment>"
