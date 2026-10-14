"""Disclosure notes of tools.host_llm (split out of host_llm.py)."""

from __future__ import annotations

import logging
from typing import NamedTuple

from tools import host_llm_allow as al

logger = logging.getLogger("tools.host_llm")


class _Tally(NamedTuple):
    """Allow-listed-unmigrated count and the constant ledger total."""

    bypassed_count: int
    total: int


def _unknown_ids_note(unknown: tuple[str, ...]) -> str:
    """Disclosure suffix for allow-list ids missing from the ledger (and a log line)."""
    if not unknown:
        return ""
    logger.warning(
        "allowed_paths() lists %d id(s) that are not in "
        "docs/LLM_MIGRATION_INVENTORY.md, so they allow NOTHING "
        "(typo?): %s",
        len(unknown),
        ", ".join(unknown),
    )
    return (
        " ⚠️ Unrecognised allow-list id(s) — not in the "
        f"ledger, so they allow NOTHING (typo?): {', '.join(unknown)}."
    )


# TRUE completion -- the only calm state. Phase-6 PREPARATION
# (2026-08-02) widened the WORDING and touched no condition. The
# master plan asked for "server LLM retired -- host model does all
# generation", which is true of GENERATION and an over-claim as a
# blanket statement: ELEVEN terminal rows read
# `disabled (disclosed)` and are real, permanent capability losses
# on an install that flips the default with no allow-list, so the
# line names them -- in PROSE.
#
# It deliberately prints NO ledger id and NO literal
# QA_SERVER_LLM_ALLOW=<ids> recipe. This branch renders on EVERY
# retired install, and this module's standing discipline (shared
# with the per-mode items in handlers/) is that an id
# is named only where THAT install actually loses the capability --
# naming one otherwise promises a loss the operator does not
# suffer. A dry-run web install and an install with both Maestro
# modes off would both be told to restore things they never had.
# Five shipped tests encode exactly that property
# (tests/test_host_boomerang_phase5b_mobile_loops.py and
# tests/test_host_boomerang_phase5c_web_verify.py assert the
# ABSENCE of those ids from qa-doctor output on installs that
# do not need them), so the concrete recipes live in the two docs
# this line points at instead.
#
# The lowercase substring "every ledger row is migrated" is kept
# VERBATIM: tests/test_host_llm.py and
# tests/test_host_boomerang_residue_r4.py assert on it, and
# relaxing those assertions to fit new prose is how a disclosure
# quietly loses the guarantee it exists to make.
def _complete_note(unknown_note: str) -> str:
    """The calm line for TRUE completion (``UNMIGRATED_PATHS`` empty)."""
    return (
        "\u2705 Server LLM retired \u2014 the tester's own chat "
        "model does all test-case generation, and every ledger row "
        "is migrated to the host model or disabled with a disclosed "
        "reason, so nothing degrades silently. Read "
        "`disabled (disclosed)` as a REAL loss, not a no-op: mobile "
        "heal/explore triage, web visual-verify adjudication, "
        "Feature-Analysis screen descriptions, the server-side "
        "vague-step rewrite and advisory gap prose, Jira comment "
        "extraction, checklist NLI re-judging, the eval judges, "
        "LangGraph intent classification, and the inert Maestro "
        "step translation stay OFF unless that specific row is "
        "named by the allow-list seam. This line lists no ids on "
        "purpose \u2014 qa-doctor names one only where THIS install "
        "really loses the capability. For the per-row ids see "
        "docs/LLM_MIGRATION_INVENTORY.md \u2192 Phase 6 sign-off "
        "(Table A) and docs/FEATURE_FLAGS.md." + unknown_note
    )


def _off_note(off: tuple, tally: _Tally, unknown_note: str) -> str:
    """The warning for rows that are genuinely OFF (unmigrated, not allow-listed)."""
    shown = ", ".join(desc for _, desc in off[:6])
    more = f" (+{len(off) - 6} more)" if len(off) > 6 else ""
    bypass_note = (
        f" A further {tally.bypassed_count} unmigrated row(s) are allow-listed "
        "and still calling the server-side backend (allow-listed is NOT "
        "migrated)."
        if tally.bypassed_count
        else ""
    )
    return (
        "\u26a0\ufe0f Server LLM disabled \u2014 "
        f"{len(off)} of {tally.total} ledger rows are NOT yet migrated, so "
        f"these features are OFF (not boomeranged): {shown}{more}."
        f"{bypass_note} Reviving them is a CODE change in "
        "llm.server_llm_enabled / host_llm.allowed_paths -- the "
        "QA_SERVER_LLM_* settings were DELETED on 2026-08-15. "
        "See docs/LLM_MIGRATION_INVENTORY.md." + unknown_note
    )


def _bypassed_note(bypassed_count: int, total: int, unknown_note: str) -> str:
    """The BYPASS warning: every unmigrated row is allow-listed, none migrated."""
    how = (
        "The allow-list seam is the `*` wildcard \u2014 the server-LLM "
        "seam is BYPASSED for every path that tags itself. `*` is "
        "debug-only and UNSUPPORTED: list the specific ledger ids you "
        "actually need instead."
        if al.wildcard_allowed()
        else "The allow-list seam names every still-unmigrated ledger "
        "row, so the server-LLM seam is BYPASSED rather than in force."
    )
    return (
        "\u26a0\ufe0f " + how + " The server LLM is retired, but "
        f"{bypassed_count} of {total} ledger rows are allow-listed and still "
        "UNMIGRATED: they keep calling the server-side backend and keep "
        "billing (allow-listed is NOT migrated \u2014 nothing moved to "
        "the host model). See docs/LLM_MIGRATION_INVENTORY.md." + unknown_note
    )
