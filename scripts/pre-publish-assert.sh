#!/usr/bin/env bash
# pre-publish-assert.sh — Pre-publish safety checks. Exit non-zero if any check fails.
# Run this before every push. All three checks must pass.
# ponytail: plain git/grep checks; no frameworks, no deps.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILURES=0

# Expected commit count for Phase A (update this when new commits are added)
# Record the count of local commits at Phase A completion:
EXPECTED_MIN_COMMITS=7
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
BAD_COMMITS=$(git -C "$REPO_ROOT" log --format="%H %ae %an" 2>/dev/null | \
  grep -v "^$PINNED_AUTHOR_EMAIL " | \
  awk -v email="$PINNED_AUTHOR_EMAIL" -v name="$PINNED_AUTHOR_NAME" \
    '$2 != email || $3 != name {print}' || true)

# Re-check using git log properly
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

# CHECK (c): local commit count is at least the expected minimum
ACTUAL_COUNT=$(git -C "$REPO_ROOT" rev-list --count HEAD 2>/dev/null || echo 0)
if [[ "$ACTUAL_COUNT" -lt "$EXPECTED_MIN_COMMITS" ]]; then
  echo "FAIL (c): expected at least $EXPECTED_MIN_COMMITS commits, found $ACTUAL_COUNT."
  FAILURES=$((FAILURES + 1))
else
  echo "PASS (c): $ACTUAL_COUNT commits present (expected >= $EXPECTED_MIN_COMMITS)."
fi

echo "==="
if [[ "$FAILURES" -gt 0 ]]; then
  echo "pre-publish-assert FAILED ($FAILURES check(s) failed). Do NOT push." >&2
  exit 1
else
  echo "pre-publish-assert PASS — safe to review before pushing."
  exit 0
fi
