"""test_no_doubled_scripts.py - one copy of every engine script, not two.

Seven scripts were tracked in BOTH Meta/sync/ and wulong/sync/ and had drifted
apart in both directions: every Meta/sync/ copy read WULONG_ROOT (2 to 3
references) and no wulong/sync/ copy read it at all, while the wulong/sync/
copies carried run_smoke, run_smoke_test, _selftest_skill_citations and about
nine validation blocks the other copies lacked. Change C0 merged them into the
shipped copy. This test is what stops the split coming back.

C0 HAS THREE COMPONENTS, NOT TWO: (1) the merge into wulong/sync/, (2) git rm of
the 7 superseded Meta/sync/ .py files, and (3) removal of the xfail(strict=True)
marker below. Components 2 and 3 must land in the SAME commit. strict-xfail fails
on SUCCESS, so deleting the files while the marker is still here turns the test
into XPASS(strict) and reds the suite just as surely as leaving the files does.
Measured: baseline 1F/134P/2S/1xfail, delete only gives 2 failed, delete plus
marker removal gives 1F/135P/2S with the sole failure a pre-existing sandbox
read-deny unrelated to this file.

STATUS: component 1 is in the tree. Components 2 and 3 are NOT yet applied, so
the marker below is still load-bearing and still correct. Do not remove it on
its own.

Meta/sync/session-close-audit-config.json is deliberately NOT covered by the
filename rule and gets its own test: it is per-VAULT configuration rather than
package data, it only ever existed in one directory, and wulong/sync/
session-close-audit.py resolves it relative to the vault root. Deleting it would
silently downgrade the audit from blocking to non-blocking, because the config
loader fails closed to block_enabled=false with no error and no warning.

ponytail: two directory listings and a set intersection. No fixtures.
"""
import pathlib


_REPO = pathlib.Path(__file__).resolve().parent.parent
_VAULT_SYNC = _REPO / "Meta" / "sync"
_PACKAGE_SYNC = _REPO / "wulong" / "sync"

_AUDIT_CONFIG_NAME = "session-close-audit-config.json"


def test_no_script_exists_in_both_sync_directories() -> None:
    """A filename in both directories is a fork waiting to happen."""
    if not _VAULT_SYNC.is_dir():
        return
    vault = {p.name for p in _VAULT_SYNC.iterdir() if p.is_file()}
    package = {p.name for p in _PACKAGE_SYNC.iterdir() if p.is_file()}
    assert not (vault & package), (
        "these filenames exist in BOTH Meta/sync/ and wulong/sync/: "
        f"{sorted(vault & package)}. C0 merged them into wulong/sync/; the "
        "superseded copies must be removed with:  "
        "git rm " + " ".join(f"Meta/sync/{n}" for n in sorted(vault & package))
    )


def test_the_audit_config_still_exists_where_the_shipped_script_looks() -> None:
    """Removing this file turns a blocking audit into a silent no-op.

    session-close-audit.py builds AUDIT_CONFIG from the resolved vault root and
    _load_audit_config() returns block_enabled=False on ANY exception, so an
    accidental delete produces no error at all, just an audit that stops
    blocking. The disposition decided in C0 is: keep it in the vault, because it
    is vault policy, not package data.
    """
    assert (_VAULT_SYNC / _AUDIT_CONFIG_NAME).is_file(), (
        f"{_AUDIT_CONFIG_NAME} is gone from Meta/sync/. The shipped audit will "
        "now fail closed to block_enabled=false without reporting anything."
    )


def test_every_shipped_script_that_resolves_a_root_reads_wulong_root() -> None:
    """The seven merged scripts must keep the WULONG_ROOT support C0 carried over.

    All seven read zero references before the merge, so a regression here means
    someone reverted to the pre-C0 copy.
    """
    merged = [
        "cerebrum-search.py",
        "query-receipts.py",
        "session-close-audit.py",
        "session-guard.py",
        "trace-change-chain.py",
        "validate-receipt-graph.py",
        "validate-receipts.py",
    ]
    missing = [
        name for name in merged
        if "WULONG_ROOT" not in (_PACKAGE_SYNC / name).read_text(encoding="utf-8")
    ]
    assert not missing, f"lost WULONG_ROOT support in: {missing}"
