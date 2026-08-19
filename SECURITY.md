# Security

Two things live here: what wulong runs when you point it at a directory, and the
standing rule for keeping this repository clean before any commit or push.

## The trust boundary: the vault you scan is trusted input

**Do not point wulong at a directory you would not run `make` in.**

wulong executes scripts resident in your vault, the way `make` executes a
Makefile. That is worth saying out loud because `doctor`, `pulse` and `gate`
read as audit verbs, and the tools you probably map them onto (ruff, mypy,
gitleaks) never run the code they inspect. wulong does. Those scripts are the
payload it exists to drive: without them the matching checks skip and the tool
does nothing, exactly as `make` without a Makefile does nothing.

Two consequences follow, and both are properties of the design rather than bugs
awaiting a fix.

**The verdict is only as trustworthy as the vault.** This is the one thing
wulong does that `make` does not. `make` never claims to have verified
anything; wulong prints a verdict, and on a tree you do not control that verdict
is chosen by whoever wrote the tree. Reproduced during review: a vault whose own
receipt validator had been replaced by a script that checks nothing and exits 0
made wulong print `[PASS] receipt schema valid`.

**wulong can run code from a directory you never named.** When you pass no path
and set no `WULONG_ROOT`, root resolution falls back to walking up from your
working directory, so the vault it settles on may be an ancestor of wherever you
happen to be standing. GNU make never searches parent directories. Pass `--root`
(or the `doctor` positional) whenever it matters which tree you are pointing at.

### What was removed in 0.4.0, and why

The D7 check dispatched project "plug-ins": it read a YAML manifest out of the
directory being scanned and passed each entry's `cmd` to a shell as a string.
That is categorically worse than running a named file by path, because the
scanned directory chose the command as well as the arguments and quoting was the
only thing standing between the two. Nothing ever shipped a manifest, so the
capability was speculative while the sink was real. It is gone, and D7 now
reports N/A on every run with that reason in the report.

Removing it did not make wulong safe against a hostile directory, and this
document does not claim that it did. The class of vault-resident execution
described above is unchanged and intended. `tests/test_execution_surface.py`
holds both halves. One half pins a count per primitive across every package
`.py` file and reds if any of them moves: shell keyword 0, the os module's
`system` and `popen` helpers 0 each, `exec` 0, `compile` 0, `exec_module` 3,
`eval` 3. The other half fails if vault-resident execution stops, so this page
and the code stay matched. That guard is an inventory of what is written at each
call site, not a proof that no shell can be reached: a shell flag handed in
through a `**kwargs` unpack is not covered by it, because the syntax tree cannot
resolve a runtime dictionary.

## The opt-in Stop hook: what you are consenting to

`wulong init` always installs `.claude/hooks/stop-slop-hook.py` as a payload
file, and never runs it. Passing `--with-hooks` additionally writes
`.claude/settings.json`, which asks Claude Code to execute that script every
time a turn ends. That is code running on your machine on a schedule you did not
set, which is why it is a flag you type rather than a default you receive.

What it does when it runs: reads the transcript path handed to it on stdin,
scans the last assistant turn for U+2014, and on a hit prints a block decision
so the model rewrites. It makes no network call, spawns no process, and reads no
file other than the transcript path in that payload.

What it writes: one JSON line per invocation appended to
`.wulong/hook-events.jsonl`, holding a UTC timestamp, the hook name, the event,
the outcome, and on an internal error the exception class name. It records a
COUNT of em dashes found, never the message text, and it writes nothing at all
unless a `.wulong/` directory already exists, so a mis-resolved root leaves no
trace instead of scattering one.

Turning it off is deleting `.claude/settings.json` or its `"Stop"` entry.
`wulong init` cannot remove it, because init never overwrites an existing file.

## Before every commit and push

This section records the CEO standing rule for keeping this repository clean
before any commit or push.

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

- (a) The `origin` remote is the expected publish target
  (`github.com/visrutsuresh/wulong`), so a push cannot go to an unintended
  remote. Set `WULONG_EXPECTED_REMOTE` to override it for a fork.
- (b) No commit author name or email matches a pattern in the scrub deny-list.
  The project is attributed to the named authors in `AUTHORS`, so this check is
  aimed at a private address, host or handle, not at a real name. It skips lines
  carrying a leading `[allow-author]` sigil. See the deny-list section below for
  why that tag exists.
- (c) The scrub deny-list in `scrub-patterns.txt` is clean across all
  git-tracked files (the inlined pattern scan). It skips lines carrying a
  leading `[allow-public]` sigil, and nothing else. It differs from
  `scripts/scrub.sh` in scope only: (c) walks `git ls-files`, so it sees
  tracked files and nothing else, while `scrub.sh` walks a directory and so also
  sees untracked files. Neither excludes a directory. `.github/` used to be
  excluded from (c) and is not any more, because that left CI workflow files,
  the likeliest home for a token, unscanned by the only check that blocks a push.

Both checks, and `scripts/scrub.sh`, strip the inline trailing comment from a
deny-list line before using it as a regex.

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

`scrub-patterns.txt` holds the deny-list of patterns the scrub checks (create it
from the template first: `cp scrub-patterns.txt.example scrub-patterns.txt`, then
fill in your private tokens). Each line is a case-insensitive regex. To add a
pattern: add a line, run the scrub over the whole tree to confirm no existing
files match, then commit the updated deny-list as a standalone `chore:` commit.

A deny-list line has this shape:

```
[tag]... <regex>     # optional trailing comment
```

Every check strips the inline trailing comment (whitespace, then `#`, to end of
line) before using the line as a regex. Tags therefore have to LEAD the line: a
tag parked in the comment is stripped with the comment and does nothing. Only an
`[allow-...]` token is read as a tag, so a pattern that legitimately opens with
a bracket expression is not mistaken for a tagged line.

### The two deny-list tags

They exempt different checks and are not interchangeable.

#### `[allow-author]`, the author check only

`docs/CONTRIBUTING.md` requires a real name on every DCO sign-off, and the
deny-list is where you register your own name. Those two rules cannot both bind
the commit author line, so tag any deny-list entry you must be able to commit
under:

```
[allow-author] \bYourFirstName\b     # first name
```

Check (b) skips tagged lines. The file scan does NOT skip them, and since 0.3.0
it genuinely enforces them, so the name is still blocked from appearing inside
a tracked file. Do not use this tag for an address, a host, an IP or a handle:
those are what check (b) exists to catch.

#### `[allow-public]`, the file scan only

Some values are public by construction and are therefore expected inside your
own files. This repository is published at `github.com/visrutsuresh/wulong`, so
the handle `visrutsuresh` appears in every clone and install URL in the README,
and `scripts/pre-publish-assert.sh` hard-codes that same URL as the remote it
requires. A copyright holder is published in LICENSE, NOTICE and AUTHORS by
design. Blocking either from appearing in a tracked file would make the publish
gate unpassable.

```
[allow-public] visrutsuresh                   # published in every repo URL
[allow-author] [allow-public] \bVisrut\b      # commit author AND copyright line
```

This tag is deliberately narrow. It skips the file scan and nothing else, so a
tagged value is still checked against the commit author line unless it also
carries `[allow-author]`. Use it only for a value you would publish on purpose.
It is not a way to silence a scan hit you have not read.

The home path is the case worth being careful about. The handle is on the
deny-list partly because it is a component of the macOS home path, and the home
path has its own separate untagged pattern in the template, which stays enforced
under every branch. Tagging the handle does not weaken it.

#### Migration from 0.1.0

0.1.0 is the only release ever published to PyPI, so it is the only version any
user can be migrating from. 0.2.0 exists in `CHANGELOG.md` as a record of work
that was folded into 0.3.0 and was never uploaded.

The tag used to sit in the trailing comment, and the file scan passed that
comment straight to `grep`, so every deny-list line was a regex that matched
nothing. If your `scrub-patterns.txt` still ends a line with
`# ... [allow-author]`, move the tag to the front. Until you do, both scripts
print a WARN naming the line, the author check stops exempting it, and the file
scan starts enforcing it. The failure is loud in both directions; nothing is
silently exempted.

### What the scanners skip

The tooling files (`scrub-patterns.txt`, `scripts/scrub.sh`,
`scripts/pre-publish-assert.sh`) are excluded from self-scanning, since they
legitimately contain the patterns they check for, as are `*.example` templates.
Those are the only exclusions. There is no exemption for `LICENSE`; the
copyright holder's name passes because it carries `[allow-public]`.
