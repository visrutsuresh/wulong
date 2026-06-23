#!/usr/bin/env bash
# pre-publish-assert.sh — Pre-publish safety checks. Exit non-zero if any check fails.
# Run this before every push. All three checks must pass.
# ponytail: plain git/grep checks; no frameworks, no deps.
#
# Checks:
#   (a) no remote configured
#   (b) all commit authors match pinned pseudonym
#   (c) scrub deny-list clean on git-tracked files (gitignored runtime files are not published)

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PATTERNS="$REPO_ROOT/scrub-patterns.txt"
FAILURES=0

PINNED_AUTHOR_NAME="wulong"
PINNED_AUTHOR_EMAIL="vault@local"
LICENSE_FILE="$REPO_ROOT/LICENSE"

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

# CHECK (c): scrub deny-list clean on git-tracked files only.
# Gitignored files (e.g. Meta/doctor/ runtime logs) are not published and not scanned.
# LICENSE: the single copyright line is allowed through (open-source attribution norm).
if [[ ! -f "$PATTERNS" ]]; then
  echo "FAIL (c): scrub-patterns.txt not found — run: cp scrub-patterns.txt.example scrub-patterns.txt"
  FAILURES=$((FAILURES + 1))
else
  SCRUB_FOUND=0
  # Tooling files that legitimately contain the deny-list patterns.
  # .github/ is also excluded: CI runner names (e.g. ubuntu-latest) may
  # substring-match personal patterns like NTU — those are false positives.
  EXCLUDED=(
    "$REPO_ROOT/scrub-patterns.txt"
    "$REPO_ROOT/scripts/scrub.sh"
    "$REPO_ROOT/scripts/pre-publish-assert.sh"
  )
  EXCLUDED_PREFIXES=(
    "$REPO_ROOT/.github/"
  )
  is_excluded() {
    local f="$1"
    for exc in "${EXCLUDED[@]}"; do [[ "$f" == "$exc" ]] && return 0; done
    for prefix in "${EXCLUDED_PREFIXES[@]}"; do [[ "$f" == "$prefix"* ]] && return 0; done
    return 1
  }

  while IFS= read -r pattern; do
    [[ -z "$pattern" || "$pattern" == \#* ]] && continue
    while IFS= read -r rel; do
      filepath="$REPO_ROOT/$rel"
      is_excluded "$filepath" && continue
      [[ ! -f "$filepath" ]] && continue
      file "$filepath" | grep -q "text" || continue

      if [[ "$filepath" == "$LICENSE_FILE" ]]; then
        # Allow the single copyright line; flag any other match
        matches=$(grep -nEi "$pattern" "$filepath" 2>/dev/null || true)
        filtered=$(printf '%s\n' "$matches" | grep -vE '^[0-9]+:Copyright \(c\) [0-9]{4} .+$' || true)
        if [[ -n "$filtered" ]]; then
          echo "SCRUB HIT (c): $rel"
          printf '%s\n' "$filtered" | head -3 | sed 's/^/  /'
          SCRUB_FOUND=1
        fi
      else
        if grep -qEi "$pattern" "$filepath" 2>/dev/null; then
          echo "SCRUB HIT (c): $rel"
          grep -nEi "$pattern" "$filepath" | head -3 | sed 's/^/  /'
          SCRUB_FOUND=1
        fi
      fi
    done < <(git -C "$REPO_ROOT" ls-files)
  done < "$PATTERNS"

  if [[ "$SCRUB_FOUND" -eq 1 ]]; then
    echo "FAIL (c): scrub deny-list found sensitive patterns in tracked files."
    FAILURES=$((FAILURES + 1))
  else
    echo "PASS (c): scrub deny-list clean on all git-tracked files."
  fi
fi

echo "==="
if [[ "$FAILURES" -gt 0 ]]; then
  echo "pre-publish-assert FAILED ($FAILURES check(s) failed). Do NOT push." >&2
  exit 1
else
  echo "pre-publish-assert PASS — safe to review before pushing."
  exit 0
fi
