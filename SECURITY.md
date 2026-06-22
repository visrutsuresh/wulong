# Security Standing Rule

This document records the CEO standing rule for keeping this repository clean
before any commit or push.

## Before every commit and push

Run all three checks in order. Do NOT push if any check fails.

### 1. Sensitive-pattern scrub

```bash
bash scripts/scrub.sh
```

This scans every file in the tree (excluding `.git/` and the tooling files
themselves) against the deny-list in `scrub-patterns.txt`. Any match prints the
file, line, and matched pattern and exits non-zero. Fix all hits before
proceeding.

### 2. Credential scan (gitleaks)

```bash
gitleaks detect --source . --verbose
```

gitleaks checks for secrets, tokens, API keys, and credentials that may have
been accidentally included. It must exit clean (no findings) before any push.

If gitleaks is not installed: `brew install gitleaks`.

### 3. Pre-publish assertion

```bash
bash scripts/pre-publish-assert.sh
```

This checks three things:

- (a) No git remote is configured (no accidental push to a public remote).
- (b) All commits are authored under the pinned pseudonym (`wulong / vault@local`),
  not a personal identity.
- (c) The commit count is at least the expected minimum for the current phase,
  confirming no commits were lost or squashed accidentally.

All three checks must PASS before pushing.

## Commit discipline

Commits are staged in small logical units with conventional prefixes:

- `feat:` for new capability
- `fix:` for a bug fix
- `chore:` for tooling, config, scaffolding
- `docs:` for documentation
- `refactor:` for restructuring with no behaviour change
- `data:` for model artifacts or data updates

Never commit one large dump. Each commit should be reviewable in isolation.

## Pattern deny-list

`scrub-patterns.txt` holds the deny-list of patterns the scrub checks. Each
line is a case-insensitive regex. To add a pattern: add a line, run the scrub
over the whole tree to confirm no existing files match, then commit the updated
deny-list as a standalone `chore:` commit.

### Copyright exemption

The LICENSE file contains the copyright holder line. The scrub exempts exactly
that one line in that one file and flags anything else. This exemption is
narrow and hard-coded in `scripts/scrub.sh` and does not weaken any other
pattern. The tooling files (scrub-patterns.txt, scripts/scrub.sh,
scripts/pre-publish-assert.sh) are also excluded from self-scanning, since
they legitimately contain the patterns they check for.
