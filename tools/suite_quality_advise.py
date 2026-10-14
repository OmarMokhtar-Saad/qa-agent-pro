"""Suite-quality advisories (leaf of tools.suite_quality_signals)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from config.settings_guard import never_raise
from tools.suite_quality_detect import (
    ac_concentration,
    conditional_steps,
    dedup_self_report_contradiction,
    precondition_repetition,
    unverified_external_references,
)

logger = logging.getLogger("tools.suite_quality_signals")


@dataclass(frozen=True)
class AdvisoryContext:
    """Optional inputs for ``compute_suite_quality_advisories``."""

    staged_text: str = ""
    host_claimed_no_duplicates: bool = False
    server_duplicate_groups: object = None


def _refs_advisory(cases, staged_text):
    refs = unverified_external_references(cases, staged_text)
    if not refs:
        return None
    return (
        "Unstaged ticket references",
        "These cases reference ticket key(s) not found in the "
        f"staged ticket text: {', '.join(refs)}.",
    )


def _conditional_advisory(cases):
    cond_ids = conditional_steps(cases)
    if not cond_ids:
        return None
    return (
        "Conditional / unexecutable steps",
        "These case(s) have a step whose action reads as an "
        "if/when branch rather than a single deterministic "
        f"action: {', '.join(cond_ids)}.",
    )


def _concentration_advisory(cases):
    ac = ac_concentration(cases)
    if not ac:
        return None
    rid, ratio = ac
    return (
        "Acceptance-criterion concentration",
        f"`{rid}` backs {ratio:.0%} of this suite -- coverage may "
        "be concentrated on one requirement rather than spread "
        "across the ticket.",
    )


def _precondition_advisory(cases):
    precond = precondition_repetition(cases)
    if not precond:
        return None
    text, ratio = precond
    return (
        "Precondition repetition",
        f"{ratio:.0%} of this suite shares the identical "
        f"precondition text ({text[:80]!r}) -- may indicate "
        "copy-paste rather than per-case tailoring.",
    )


def _dedup_advisory(host_claimed_no_duplicates, server_duplicate_groups):
    if not dedup_self_report_contradiction(
        host_claimed_no_duplicates, server_duplicate_groups
    ):
        return None
    return (
        "Dedup self-report contradiction",
        "The submitting host reported NO duplicate cases, but "
        "the server's own lexical prescreen found candidate "
        "duplicate group(s) -- the two disagree.",
    )


# Aggregate every signal above into ``(title, detail)`` tuples, the exact
# shape the Generation Notes extension point already takes
# (``handlers/``'s ``_gen_notes.append((title, detail))``, see
# ``agents.host_jobs.ambiguity_notes_gen_note`` for a sibling of this same
# shape). [] when nothing is worth flagging, including for a None/garbage
# ``cases`` argument. Warn-only -- never refuses, never raises.
def compute_suite_quality_advisories(
    cases, ctx: AdvisoryContext | None = None
) -> list[tuple[str, str]]:
    """Aggregate every signal into ``(title, detail)`` tuples; never raises."""
    ctx = ctx or AdvisoryContext()
    out: list[tuple[str, str]] = []
    with never_raise(logger, "compute_suite_quality_advisories failed", exc_info=True):
        found = (
            _refs_advisory(cases, ctx.staged_text),
            _conditional_advisory(cases),
            _concentration_advisory(cases),
            _precondition_advisory(cases),
            _dedup_advisory(
                ctx.host_claimed_no_duplicates, ctx.server_duplicate_groups
            ),
        )
        out.extend(a for a in found if a)
    return out
