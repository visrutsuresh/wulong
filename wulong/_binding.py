"""Is a `review_verdict: PASS` bound to the artifact it reviewed?

ONE predicate for every gate that keys off `review_verdict`, for the same reason
`wulong/_frontmatter.py` is one reader: fourteen comparison sites across four
scripts decide the same question, and fourteen copies of a migration rule is
fourteen chances to disagree about what a PASS means.

WHAT THIS IS NOT. The vocabulary does not become "binding" when the fields are
present. It stays ADVISORY WITH ATTESTATION. The digest is unkeyed, so anyone
who can write a receipt can read the artifact and compute its digest, which is
the same trust boundary fact 1 of "What the gate actually proves" has always
described. What the digest adds is a defence against SUBSTITUTION: reviewing
plan A then shipping plan B, reusing one change_id's PASS for a different
artifact, and editing an artifact after the PASS was written. The three limits
are disclosed in `wulong/_manifest.py` and in both published documents.

MIGRATION, and why it is a mechanism rather than paperwork. `DEFAULT_REQUIRE_
BINDING` is False, so today an unbound PASS still passes and prints a warning.
`BINDING_REQUIRED_FROM` is the 0.6.0 flip date and it is a CONSTANT IN CODE, not
a sentence in a changelog no code reads. `binding_default_is_current` goes False
once that date passes while the default is still off, and one test asserts it,
so CI reds and someone has to act. CI runs pytest with
`WULONG_REQUIRE_NO_SKIPS=1` and `tests/conftest.py` turns any skip into a session
failure, so the tripwire cannot be quietly neutered with a skip marker.

The one honest hole in that mechanism: if nobody commits again, CI never runs
and the tripwire never fires. A date constant compels a maintainer who is still
working on the project. It cannot compel one who has stopped.

`WULONG_LEGACY_UNBOUND_UNTIL` is ADVISORY and is documented as such wherever it
appears. It exempts receipts dated before its value, and that date is the
receipt's OWN self-reported `date` field. A writer who can mint the verdict can
mint the date beside it, so the exemption is a convenience for a real corpus of
old receipts and never a control.

ponytail: stdlib only, module-level constants, no config file and no registry.
The ceiling is one boolean default plus one date. The upgrade path at 0.6.0 is
to set `DEFAULT_REQUIRE_BINDING = True` and delete the tripwire test.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import date
from typing import Mapping, Optional

# Frontmatter field names. Written by `wulong gate --manifest`, read here.
FIELD_DIGEST = "artifact_manifest_sha256"
FIELD_COUNT = "artifact_count"
# Diagnostic only. No verifier resolves it, and nothing in this module reads it.
FIELD_PATHS = "artifact_paths"
FIELD_DATE = "date"

ENV_REQUIRE = "WULONG_REQUIRE_BINDING"
ENV_LEGACY_UNTIL = "WULONG_LEGACY_UNBOUND_UNTIL"

# OFF for the migration window. Flips at 0.6.0, on the date below.
DEFAULT_REQUIRE_BINDING = False

# The 0.6.0 flip date. CHANGELOG.md carries this same date in prose and a test
# cross-checks the two, so the constant and the published promise cannot drift.
BINDING_REQUIRED_FROM = date(2027, 2, 1)

_HEX64 = re.compile(r"\A[0-9a-f]{64}\Z")

# Loud, and bounded. An unbound corpus would otherwise print one line per
# receipt and teach everyone to pipe stderr to /dev/null, which is quieter than
# silence.
_WARN_LIMIT = 5
_warned = 0


def reset_warnings() -> None:
    """Reset the per-process warning budget. For tests, which run in one process."""
    global _warned
    _warned = 0


def _warn(message: str) -> None:
    global _warned
    _warned += 1
    if _warned <= _WARN_LIMIT:
        print(f"[wulong] WARN: {message}", file=sys.stderr)
    elif _warned == _WARN_LIMIT + 1:
        print(
            "[wulong] WARN: further unbound-verdict warnings suppressed for this "
            f"process (limit {_WARN_LIMIT}). Run `wulong gate --manifest` to stamp "
            "the receipts.",
            file=sys.stderr,
        )


def binding_default_is_current(today: date) -> bool:
    """False once BINDING_REQUIRED_FROM has passed and the default is still off.

    The tripwire. It reads the same two symbols `require_binding` reads, so a
    test cannot pass by agreeing with a copy of the rule instead of the rule.
    """
    return DEFAULT_REQUIRE_BINDING or today < BINDING_REQUIRED_FROM


def require_binding(explicit: Optional[bool] = None) -> bool:
    """Is an unbound PASS refused? Explicit flag, then env var, then the default."""
    if explicit is not None:
        return explicit
    raw = os.environ.get(ENV_REQUIRE, "").strip().lower()
    if raw:
        return raw not in ("0", "false", "no", "off")
    return DEFAULT_REQUIRE_BINDING


def _text(fields: Mapping, key: str) -> str:
    """One field as stripped text. Callers hand in dicts whose values are not
    all strings: judge-score coerces a `[a, b]` value to a list and the graph
    validator stores a parsed `date` object."""
    value = fields.get(key, "")
    if value is None:
        return ""
    return str(value).strip()


def is_bound(fields: Mapping) -> bool:
    """True when this receipt carries a well-formed manifest digest and count.

    Shape is checked here rather than trusted: a malformed digest is not a weak
    binding, it is no binding, and treating `artifact_sha256: banana` as bound
    would make the whole field decorative.
    """
    if not _HEX64.match(_text(fields, FIELD_DIGEST)):
        return False
    count = _text(fields, FIELD_COUNT)
    return count.isdigit() and int(count) >= 1


def _legacy_until() -> Optional[date]:
    raw = os.environ.get(ENV_LEGACY_UNTIL, "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def legacy_exempt(fields: Mapping) -> bool:
    """ADVISORY exemption for old receipts, keyed on the receipt's OWN date field.

    Self-reported, therefore never a control. Absent or unparseable date means
    NOT exempt, so the failure direction is closed.
    """
    cutoff = _legacy_until()
    if cutoff is None:
        return False
    try:
        written = date.fromisoformat(_text(fields, FIELD_DATE)[:10])
    except ValueError:
        return False
    return written < cutoff


def binding_ok(
    fields: Mapping,
    *,
    label: str = "",
    require: Optional[bool] = None,
) -> bool:
    """Does this receipt satisfy the artifact-binding requirement?

    Call this ONLY once the caller has already decided the verdict reads PASS.
    It answers the binding question alone and never re-decides the verdict, so
    routing a site through it cannot move a verdict while binding is off.
    """
    if is_bound(fields):
        return True
    name = label or "receipt"
    if legacy_exempt(fields):
        _warn(
            f"{name}: PASS with no artifact binding, exempted by "
            f"{ENV_LEGACY_UNTIL}. That exemption is ADVISORY: it keys off the "
            "receipt's own self-reported date."
        )
        return True
    if require_binding(require):
        _warn(
            f"{name}: PASS carries no {FIELD_DIGEST}, so it is not bound to any "
            "artifact. REFUSED under the binding requirement."
        )
        return False
    _warn(
        f"{name}: PASS carries no {FIELD_DIGEST}, so it authorises anything "
        f"sharing its change_id. Allowed for now; refused from "
        f"{BINDING_REQUIRED_FROM.isoformat()}. Stamp it with "
        "`wulong gate --manifest --artifact PATH`."
    )
    return True


def reads_pass(fields: Mapping) -> bool:
    """The RAW verdict, asked before the binding question and never instead of it.

    Only for explaining a refusal. A gate that refuses an unbound PASS must not
    then tell the reader the receipt says FAIL, because it says PASS. The
    comparison is the same one `verdict_is_binding_pass` makes, so the
    explanation cannot disagree with the decision it explains.
    """
    return _text(fields, "review_verdict") == "PASS"


def verdict_is_binding_pass(
    fields: Mapping,
    *,
    label: str = "",
    require: Optional[bool] = None,
) -> bool:
    """True when the verdict reads exactly PASS and that PASS is usable.

    Drop-in for `fields.get("review_verdict", "").strip() == "PASS"`. The verdict
    comparison is byte-identical to the one it replaces, so with binding off the
    answer is identical too.
    """
    if _text(fields, "review_verdict") != "PASS":
        return False
    return binding_ok(fields, label=label, require=require)


def _demo() -> None:
    """Runnable check: off allows and warns, on refuses, a bound PASS always passes."""
    unbound = {"review_verdict": "PASS"}
    bound = {"review_verdict": "PASS", FIELD_DIGEST: "0" * 64, FIELD_COUNT: "3"}
    assert verdict_is_binding_pass(unbound, require=False) is True
    assert verdict_is_binding_pass(unbound, require=True) is False
    assert verdict_is_binding_pass(bound, require=True) is True
    assert verdict_is_binding_pass({"review_verdict": "FAIL"}, require=False) is False
    assert is_bound({FIELD_DIGEST: "banana", FIELD_COUNT: "1"}) is False
    assert is_bound({FIELD_DIGEST: "A" * 64, FIELD_COUNT: "1"}) is False
    assert reads_pass(unbound) is True and verdict_is_binding_pass(unbound, require=True) is False
    assert binding_default_is_current(BINDING_REQUIRED_FROM) is DEFAULT_REQUIRE_BINDING
    reset_warnings()
    print("binding _demo OK")


if __name__ == "__main__":
    _demo()
