#!/usr/bin/env bash
# pre-publish-assert.sh — Pre-publish safety checks. Exit non-zero if any check fails.
# Run this before every push. All three checks must pass.
# ponytail: plain git/grep checks; no frameworks, no deps.
#
# Checks:
#   (a) origin is the expected publish target and is the only remote
#   (b) no commit author name or email matches a scrub deny-list pattern
#   (c) scrub deny-list clean on git-tracked files (gitignored runtime files are not published)

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PATTERNS="$REPO_ROOT/scrub-patterns.txt"
FAILURES=0

# The project is published, so "no remote" is not the safe state; "exactly the
# intended remote" is. Override for a fork.
EXPECTED_REMOTE="${WULONG_EXPECTED_REMOTE:-github.com/visrutsuresh/wulong}"
# Deny-list line format: optional leading [tag] sigils, then the regex, then an
# optional inline trailing comment. Both checks below strip the comment before
# using the line as a regex, so the sigils have to LEAD the line: a tag parked
# in the comment would be stripped with it.
# Only an [allow-...] token counts as a sigil, so a pattern that legitimately
# opens with a bracket expression is not mistaken for a tagged line.
# The same two helpers exist in scripts/scrub.sh. Fixing one script and not the
# other leaves the other one inert.
denylist_tags() {
  printf '%s' "$1" | sed -nE 's/^(([[:space:]]*\[allow-[a-z]+\])+).*$/\1/p'
}
denylist_regex() {
  printf '%s' "$1" | sed -E 's/^([[:space:]]*\[allow-[a-z]+\])+[[:space:]]*//; s/[[:space:]]+#.*$//; s/[[:space:]]*$//'
}

echo "=== pre-publish-assert.sh ==="

# CHECK (a): origin is the expected publish target, and nothing else is configured
ORIGIN_URL="$(git -C "$REPO_ROOT" config --get remote.origin.url 2>/dev/null)"
OTHER_REMOTES="$(git -C "$REPO_ROOT" remote 2>/dev/null | grep -v '^origin$')"

if [[ -z "$ORIGIN_URL" ]]; then
  echo "FAIL (a): no 'origin' remote configured (expected $EXPECTED_REMOTE)."
  FAILURES=$((FAILURES + 1))
elif [[ "$ORIGIN_URL" != *"$EXPECTED_REMOTE"* ]]; then
  echo "FAIL (a): origin is '$ORIGIN_URL', expected it to contain '$EXPECTED_REMOTE'."
  FAILURES=$((FAILURES + 1))
elif [[ -n "$OTHER_REMOTES" ]]; then
  echo "FAIL (a): unexpected extra remote(s) configured:"
  printf '%s\n' "$OTHER_REMOTES" | sed 's/^/  /'
  FAILURES=$((FAILURES + 1))
else
  echo "PASS (a): origin is the expected publish target ($ORIGIN_URL)."
fi

# CHECK (b): no commit author identity matches a scrub deny-list pattern.
# Authorship is now a stated goal (see AUTHORS), so a real name is fine. What
# must not leak is a private address, host or handle from the deny-list.
# A deny-list entry you must be able to commit under (your own name) carries a
# leading [allow-author] sigil and is skipped by (b) only. It is still enforced
# inside files by (c); [allow-public] is the tag that exempts (c).
if [[ ! -f "$PATTERNS" ]]; then
  echo "FAIL (b): scrub-patterns.txt not found. Run: cp scrub-patterns.txt.example scrub-patterns.txt"
  FAILURES=$((FAILURES + 1))
else
  AUTHORS_SEEN="$(git -C "$REPO_ROOT" log --format="%H %an <%ae>" 2>/dev/null)"
  BAD_COMMITS=""
  while IFS= read -r raw; do
    [[ -z "$raw" || "$raw" == \#* ]] && continue
    # Deny-lists written before 0.3.0 put the tag inside the trailing comment,
    # where it is now stripped along with the comment. Warn once, here, rather
    # than silently changing what that line means in either check.
    if [[ "$raw" == *"#"*"[allow-"* ]]; then
      echo "WARN: deny-list tag is inside a comment and will be ignored."
      echo "      Move it to the front of the line: $raw"
    fi
    # A name the DCO tells you to commit under cannot also be forbidden in your
    # own author line, so a leading [allow-author] sigil skips this check.
    [[ "$(denylist_tags "$raw")" == *"[allow-author]"* ]] && continue
    # Strip the leading sigils and the inline trailing comment. Without this the
    # comment is part of the regex, which can never match a one-line author
    # string, and (b) prints PASS for every input.
    pattern="$(denylist_regex "$raw")"
    [[ -z "$pattern" ]] && continue
    hits=$(printf '%s\n' "$AUTHORS_SEEN" | grep -Ei -- "$pattern" 2>/dev/null)
    [[ -n "$hits" ]] && BAD_COMMITS+="$hits"$'\n'
  done < "$PATTERNS"

  if [[ -n "${BAD_COMMITS// /}" ]]; then
    echo "FAIL (b): commit author identities match the scrub deny-list:"
    printf '%s' "$BAD_COMMITS" | sort -u | head -10 | sed 's/^/  /'
    FAILURES=$((FAILURES + 1))
  else
    echo "PASS (b): no commit author matches the scrub deny-list."
  fi
fi

# CHECK (c): scrub deny-list clean on git-tracked files only.
# Gitignored files (e.g. Meta/doctor/ runtime logs) are not published and not scanned.
if [[ ! -f "$PATTERNS" ]]; then
  echo "FAIL (c): scrub-patterns.txt not found — run: cp scrub-patterns.txt.example scrub-patterns.txt"
  FAILURES=$((FAILURES + 1))
else
  SCRUB_FOUND=0
  # Tooling files that legitimately contain the deny-list patterns.
  # .github/ is NOT excluded. It used to be, on the grounds that a CI runner
  # name like ubuntu-latest can substring-match a short personal pattern. That
  # was harmless only while this scan was inert. Now that it enforces, skipping
  # the workflow directory would leave the one place CI tokens live unscanned by
  # the gate that blocks a push. A pattern that false-positives on ubuntu-latest
  # is a pattern that needs a word boundary, not a directory the scanner skips.
  EXCLUDED=(
    "$REPO_ROOT/scrub-patterns.txt"
    "$REPO_ROOT/scripts/scrub.sh"
    "$REPO_ROOT/scripts/pre-publish-assert.sh"
  )
  is_excluded() {
    local f="$1"
    for exc in "${EXCLUDED[@]}"; do [[ "$f" == "$exc" ]] && return 0; done
    # .example files are template stubs that model what private tokens look like.
    # They contain placeholder strings, not real personal data, so scrub skips them.
    [[ "$f" == *.example ]] && return 0
    return 1
  }

  while IFS= read -r raw; do
    [[ -z "$raw" || "$raw" == \#* ]] && continue
    # [allow-public] is the only tag this scan honours. It means the value is
    # public by construction (a published repo URL, an attribution line), so it
    # is expected inside tracked files. [allow-author] has no effect here: a
    # name you commit under is still blocked from appearing inside a file.
    [[ "$(denylist_tags "$raw")" == *"[allow-public]"* ]] && continue
    pattern="$(denylist_regex "$raw")"
    [[ -z "$pattern" ]] && continue
    while IFS= read -r rel; do
      filepath="$REPO_ROOT/$rel"
      is_excluded "$filepath" && continue
      [[ ! -f "$filepath" ]] && continue
      file "$filepath" | grep -q "text" || continue

      if grep -qEi "$pattern" "$filepath" 2>/dev/null; then
        echo "SCRUB HIT (c): $rel"
        grep -nEi "$pattern" "$filepath" | head -3 | sed 's/^/  /'
        SCRUB_FOUND=1
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
