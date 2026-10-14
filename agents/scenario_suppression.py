"""Disclosure of the server-side review steps a host submit does not run."""

from __future__ import annotations

import logging

from agents.scenario_status import _BEST_EFFORT_ERRORS
from tools.models import TestCase
from tools.quality_checks import find_vague_expected, find_vague_steps

logger = logging.getLogger("agents.test_scenario_agent")


_VAGUE_LINE = (
    "- **Vague step text was flagged, not rewritten.** The pass that "
    "rewrote 'an appropriate error message' into a concrete, "
    "checkable outcome was retired and no longer exists in any "
    "mode. The Data Quality Notes above count every one and "
    "list examples -- tighten those steps (or ask me to) before "
    "anyone executes the suite."
)


def _coverage_line(deterministic_coverage: bool) -> str:
    """The always-present line: no LLM coverage-gap review ran on this server."""
    tail = (
        "the requirement-coverage table above is this run's coverage report"
        if deterministic_coverage
        else "nothing else in this reply reports coverage-gap findings"
    )
    return (
        "- **No LLM coverage-gap review ran on this server.** Neither "
        "the advisory coverage-gap critique nor the bounded "
        "critic/regeneration loop is a host-mode step, so "
        f"{tail}. Ask me to re-read the finished suite against the "
        "requirements if you want a second opinion."
    )


# _host_suppression_section: disclose the SERVER-SIDE review steps that do not run
# on a submit.
#
# Residue R2. Ledger rows ``test_scenario_agent.rewrite_vague`` and
# ``test_scenario_agent.markdown`` are both DISABLED -- no host job replaces
# either -- and until R2 the loss was completely SILENT. A suppression that is
# disclosed nowhere is itself a defect (Phase 3b's review finding):
# `disabled (disclosed)` is only true if something discloses. This is that
# something, and it is deliberately on the SUBMIT reply rather than in
# ``_host_mode_server_llm_notice``: neither loss is knowable at prepare time,
# because whether any field is vague depends on a suite the host has not
# written yet, and announcing a loss before the fact is the same class of
# dishonesty as never announcing it.
#
# 2026-08-16 (dead-code deletion P2-E1): the ``rewrite_vague`` and
# ``advisory_gaps`` keywords are GONE, and with them the last branch that
# could return "". They were False on the only surviving caller (the host
# submit) and True only for the server-mode orchestrator, so this function's
# output is unchanged for every path a tester can reach. The code they gated
# -- ``_rewrite_vague_fields`` and ``analyze_coverage_gaps`` -- was deleted in
# the same batch, so the disclosure now describes a capability this server
# does not HAVE rather than one it declines to use. The tester-facing wording
# is byte-identical either way: it says the call is not made, which is still
# exactly what happens.
#
# Each line stays NARROWED to what actually happened (Phase 3b/3c discipline):
#
# * The vague-field line needs an actually-vague field, judged by the SAME two
#   detectors ``_rewrite_vague_fields`` used before it was deleted. With
#   nothing vague there was never a call to lose, and reporting a loss would
#   fabricate one.
#   Note what is NOT lost: ``quality_warning_section`` runs unconditionally, so
#   the vague fields are still FLAGGED in the Data Quality Notes above. Only the
#   automatic rewrite is gone.
# * The coverage-gap line always fires, because that prose is never produced
#   here and there is no "nothing happened" case -- but its closing clause
#   names the deterministic requirement-coverage table when there IS one, so a
#   run that still carries a coverage report is never told it lost its only
#   coverage view.
#
# Deliberately avoids the literal string "Coverage Gaps":
# tests/test_host_mode_submit.py asserts on ``summary.index("Coverage Gaps")``
# to prove the reply cap cannot delete the quality block, and a second
# occurrence upstream of that heading would silently change what that index
# measures.
def _suppression_lines(
    cases: list[TestCase], *, deterministic_coverage: bool
) -> list[str]:
    """The bullet lines naming each server-side review step that did not run."""
    lines: list[str] = []
    if find_vague_steps(cases) or find_vague_expected(cases):
        lines.append(_VAGUE_LINE)
    lines.append(_coverage_line(deterministic_coverage))
    return lines


def _suppression_block(lines: list[str]) -> str:
    """The reply section holding *lines* ("" when there are none)."""
    if not lines:
        return ""
    return (
        "\n\n## Server-Side Review Steps Not Run\n\n"
        + "\n".join(lines)
        + "\n\n> These are host mode's deliberate cost/latency tradeoff, "
        "not a failure. See docs/LLM_MIGRATION_INVENTORY.md rows "
        "`test_scenario_agent.rewrite_vague` and "
        "`test_scenario_agent.markdown`."
    )


# See the comment block above ``_suppression_lines`` for the history. Never
# raises -- a disclosure must not be able to break a submit.
def _host_suppression_section(
    cases: list[TestCase],
    *,
    deterministic_coverage: bool,
) -> str:
    """Disclose the SERVER-SIDE review steps that do not run on a submit."""
    try:
        lines = _suppression_lines(cases, deterministic_coverage=deterministic_coverage)
        return _suppression_block(lines)
    except _BEST_EFFORT_ERRORS:  # pragma: no cover - disclosure must never break
        logger.debug("_host_suppression_section failed", exc_info=True)
        return ""
