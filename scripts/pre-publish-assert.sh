#!/usr/bin/env bash
# pre-publish-assert.sh — Pre-publish safety checks. Exit non-zero if any check fails.
# Run this before every push. All three checks must pass.
# ponytail: plain git/grep checks; no frameworks, no deps.
#
# Checks:
#   (a) no remote configured
#   (b) all commit authors match pinned pseudonym
#   (c) scrub deny-list clean (inlined from scrub.sh — scrub is the guard, not commit count)

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILURES=0

PINNED_AUTHOR_NAME="wulong"
PINNED_AUTHOR_EMAIL="vault@local"

echo "=== pre-publish-assert.sh ==="

# CHECK (a): no remote configured
if git -C "$REPO_ROOT" config --get-all remote.origin.url >/dev/null 2>&1; then
  echo "FAIL (a): .git/config contains [remote] — remove all remotes before publishing."
  FAILURES=$((FAILURES + 1))
else
  echo "PASS (a): no remote configured."
fi

# CHECK (b): all commit authors match pinned pseudonym
BAD_COMMITS=$(git -C "$REPO_ROOT" log --format="%H|%ae|%an" 2>/dev/null | while IFS='|' read -r hash email name; do
  if [[ "$email" != "$PINNED_AUTHOR_EMAIL" || "$name" != "$PINNED_AUTHOR_NAME" ]]; then
    echo "$hash author=$name <$email>"
  fi
done)

if [[ -n "$BAD_COMMITS" ]]; then
  echo "FAIL (b): commits with wrong author identity:"
  echo "$BAD_COMMITS"
  FAILURES=$((FAILURES + 1))
else
  echo "PASS (b): all commits authored by $PINNED_AUTHOR_NAME <$PINNED_AUTHOR_EMAIL>."
fi

# CHECK (c): scrub deny-list clean
SCRUB_RESULT=$(bash "$REPO_ROOT/scripts/scrub.sh" "$REPO_ROOT" 2>&1)
SCRUB_EXIT=$?
if [[ "$SCRUB_EXIT" -ne 0 ]]; then
  echo "FAIL (c): scrub deny-list found sensitive patterns:"
  echo "$SCRUB_RESULT"
  FAILURES=$((FAILURES + 1))
else
  echo "PASS (c): scrub deny-list clean."
fi

echo "==="
if [[ "$FAILURES" -gt 0 ]]; then
  echo "pre-publish-assert FAILED ($FAILURES check(s) failed). Do NOT push." >&2
  exit 1
else
  echo "pre-publish-assert PASS — safe to review before pushing."
  exit 0
fi
