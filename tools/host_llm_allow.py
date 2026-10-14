"""Allow-list seams of tools.host_llm (split out of host_llm.py)."""

from __future__ import annotations

from tools import host_llm_ledger as lg
from tools import host_llm_unmigrated as um


def server_llm_retired() -> bool:
    """Always False since 2026-08-15: the kill switch was DELETED, not pinned.

    QA_SERVER_LLM_ENABLED is gone and ``llm.server_llm_enabled()`` is a True
    constant, so no install is in the 'retired' state this predicate
    described. Retained as a NAMED SEAM: qa-doctor and the startup
    disclosure both branch on it. Never raises.
    """
    return False


def allowed_paths() -> frozenset:
    """Always empty since 2026-08-15: QA_SERVER_LLM_ALLOW was DELETED.

    No ledger id can be re-permitted from `.env`; reviving a path is a CODE
    change. Retained as a NAMED SEAM because the disclosure surface branches
    on emptiness. Never raises.
    """
    return frozenset()


def wildcard_allowed() -> bool:
    """True when QA_SERVER_LLM_ALLOW is/contains the `*` wildcard.

    Its own DISCLOSED state, not a synonym for "everything is fine": `*` bypasses
    the kill switch for every path that tags itself while migrating nothing. It is
    debug-only and unsupported. Never raises.
    """
    return "*" in allowed_paths()


def unknown_allow_ids() -> tuple[str, ...]:
    """QA_SERVER_LLM_ALLOW entries that are neither `*` nor a known ledger id.

    Almost always a typo (`maestro_healer.classfy`), which silently leaves that
    path disabled with no signal beyond a long pending list. Named in the
    disclosure and logged as a WARNING. Never raises.
    """
    return tuple(
        sorted(i for i in allowed_paths() if i != "*" and i not in lg.LEDGER_IDS)
    )


def pending_paths() -> tuple[tuple[str, str], ...]:
    """Unmigrated ledger rows not kept alive by an EXPLICIT allow-list id.

    The `*` wildcard is deliberately NOT honoured here. `*` means "bypass the kill
    switch", not "these rows migrated", so letting it empty this tuple would make
    the pending list read as if the migration had finished. Rows that ARE kept
    alive by the allow-list -- by `*` or by an explicit id -- are reported by
    ``allowed_and_unmigrated`` instead, and always produce a warning of their own.

    This is the SINGLE SOURCE of the "genuinely OFF" projection, not a
    test-only helper: production reads it through ``disclosure_state``, which
    derives its ``off`` set by subtracting ``allowed_and_unmigrated()`` from this
    tuple instead of recomputing the rule from ``um.UNMIGRATED_PATHS`` a second way.
    One rule, one place, so the two cannot drift apart. Never raises.
    """
    allow = allowed_paths()
    return tuple((k, d) for k, d in um.UNMIGRATED_PATHS if k not in allow)


def allowed_and_unmigrated() -> tuple[tuple[str, str], ...]:
    """Unmigrated ledger rows that QA_SERVER_LLM_ALLOW still routes to a backend.

    The GENERAL form of the "allowed is not migrated" hole, derived from the
    CONDITION (allowed AND unmigrated) rather than from the literal value `*`. An
    explicit allow-list that happens to name every unmigrated row is exactly as
    misleading as `*` -- zero rows actually migrated, every one of them still
    billing server-side -- so both must reach the same warning. Never raises.
    """
    allow = allowed_paths()
    wildcard = "*" in allow
    return tuple((k, d) for k, d in um.UNMIGRATED_PATHS if wildcard or k in allow)
