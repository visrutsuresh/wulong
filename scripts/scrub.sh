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
  # .example files are template stubs that model what private tokens look like.
  # They contain placeholder strings, not real personal data, so scrub skips them.
  [[ "$f" == *.example ]] && return 0
  return 1
}

# Deny-list line format: optional leading [tag] sigils, then the regex, then an
# optional inline trailing comment. The sigils LEAD the line because the comment
# is stripped before the line is used as a regex; a tag parked in the comment
# would be stripped with it. Passing the comment through to grep is what made
# this scan match nothing at all before 0.3.0.
# Only an [allow-...] token counts as a sigil, so a pattern that legitimately
# opens with a bracket expression is not mistaken for a tagged line.
# The same two helpers exist in check (c) of scripts/pre-publish-assert.sh.
# Fixing one script and not the other leaves the other one inert.
denylist_tags() {
  printf '%s' "$1" | sed -nE 's/^(([[:space:]]*\[allow-[a-z]+\])+).*$/\1/p'
}
denylist_regex() {
  printf '%s' "$1" | sed -E 's/^([[:space:]]*\[allow-[a-z]+\])+[[:space:]]*//; s/[[:space:]]+#.*$//; s/[[:space:]]*$//'
}

while IFS= read -r raw; do
  # Skip blank lines and whole-line comments
  [[ -z "$raw" || "$raw" == \#* ]] && continue

  # Deny-lists written before 0.3.0 put the tag inside the trailing comment,
  # where it is now stripped along with the comment. Say so out loud rather than
  # silently changing what that line means.
  if [[ "$raw" == *"#"*"[allow-"* ]]; then
    echo "WARN: deny-list tag is inside a comment and will be ignored." >&2
    echo "      Move it to the front of the line: $raw" >&2
  fi

  tags="$(denylist_tags "$raw")"

  # [allow-public] is the only tag this scan honours. It means the value is
  # public by construction (a published repo URL, an attribution line), so it is
  # expected inside tracked files. [allow-author] exempts the commit-author
  # check in pre-publish-assert.sh and has no effect here: a name you commit
  # under is still blocked from appearing inside a file.
  if [[ "$tags" == *"[allow-public]"* ]]; then
    continue
  fi

  pattern="$(denylist_regex "$raw")"
  [[ -z "$pattern" ]] && continue

  # Search files (exclude .git dir)
  while IFS= read -r -d '' filepath; do
    # Skip the pattern file itself and other excluded tooling files
    is_excluded "$filepath" && continue

    # Skip binary files
    if ! file "$filepath" | grep -q "text"; then
      continue
    fi

    if grep -nEi "$pattern" "$filepath" 2>/dev/null | grep -q .; then
      echo "SCRUB HIT: $filepath"
      grep -nEi "$pattern" "$filepath" | while IFS= read -r line; do printf "  pattern=%s match: %s\n" "$pattern" "$line"; done
      FOUND=1
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
