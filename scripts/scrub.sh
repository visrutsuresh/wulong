#!/usr/bin/env bash
# scrub.sh — Scan a path against scrub-patterns.txt. Fail-closed: any match = exit 1.
# Usage: bash scripts/scrub.sh [path]   (default: whole tree excluding .git)
# ponytail: plain grep -nEi loop over pattern file; no classes, no frameworks.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PATTERNS="$REPO_ROOT/scrub-patterns.txt"
SCAN_PATH="${1:-$REPO_ROOT}"

if [[ ! -f "$PATTERNS" ]]; then
  echo "ERROR: scrub-patterns.txt not found." >&2
  echo "  Run: cp scrub-patterns.txt.example scrub-patterns.txt" >&2
  echo "  Then fill in your private tokens before running scrub." >&2
  exit 1
fi

FOUND=0

# Build list of always-excluded files (the tooling files that legitimately contain patterns)
EXCLUDED_FILES=(
  "$REPO_ROOT/scrub-patterns.txt"
  "$REPO_ROOT/scripts/scrub.sh"
  "$REPO_ROOT/scripts/pre-publish-assert.sh"
)

is_excluded() {
  local f="$1"
  for exc in "${EXCLUDED_FILES[@]}"; do
    [[ "$f" == "$exc" ]] && return 0
  done
  return 1
}

LICENSE_FILE="$REPO_ROOT/LICENSE"

while IFS= read -r pattern; do
  # Skip blank lines and comments
  [[ -z "$pattern" || "$pattern" == \#* ]] && continue

  # Search files (exclude .git dir)
  while IFS= read -r -d '' filepath; do
    # Skip the pattern file itself and other excluded tooling files
    is_excluded "$filepath" && continue

    # Skip binary files
    if ! file "$filepath" | grep -q "text"; then
      continue
    fi

    # For the LICENSE file: allow the single copyright line through, flag anything else
    if [[ "$filepath" == "$LICENSE_FILE" ]]; then
      matches=$(grep -nEi "$pattern" "$filepath" 2>/dev/null || true)
      # Remove any copyright line from matches (generic: year + any name)
      filtered=$(echo "$matches" | grep -vE '^[0-9]+:Copyright \(c\) [0-9]{4} .+$' || true)
      if [[ -n "$filtered" ]]; then
        echo "SCRUB HIT: $filepath"
        echo "$filtered" | sed "s/^/  pattern='$pattern' match: /"
        FOUND=1
      fi
    else
      if grep -nEi "$pattern" "$filepath" 2>/dev/null | grep -q .; then
        echo "SCRUB HIT: $filepath"
        grep -nEi "$pattern" "$filepath" | sed "s/^/  pattern='$pattern' match: /"
        FOUND=1
      fi
    fi
  done < <(find "$SCAN_PATH" -not -path '*/.git/*' -type f -print0)

done < "$PATTERNS"

if [[ "$FOUND" -eq 0 ]]; then
  echo "scrub CLEAN -- no sensitive patterns found in: $SCAN_PATH"
  exit 0
else
  echo "scrub FAILED -- sensitive patterns found. Fix before publishing." >&2
  exit 1
fi
