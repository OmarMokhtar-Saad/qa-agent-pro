"""Rule-pack post-generation content enforcement and the manual-validation case."""

from __future__ import annotations

import logging

from tools.bilingual import (
    build_manual_validation_case,
    substitute_placeholders,
    sweep_residual_placeholders,
)
from tools.bilingual import new_report as new_bilingual_report
from tools.models import TestCase
from tools.rule_packs_model import RulePackResult

logger = logging.getLogger("tools.rule_packs")


def _substitute(cases: list[TestCase], result: RulePackResult, ctx: dict) -> list:
    """Substitute the documented EN/AR strings; unchanged cases on failure."""
    out = list(cases or [])
    try:
        if result.bilingual_on and result.pairs:
            out, ctx["bilingual"] = substitute_placeholders(out, result.pairs)
    except Exception:
        logger.exception("apply_rule_packs substitution failed - cases unchanged")
        out = list(cases or [])
    return out


def _sweep(out: list[TestCase], result: RulePackResult, ctx: dict) -> list:
    """Residual-token sweep, OUTSIDE the substitution try (see apply_rule_packs)."""
    if result.bilingual_on:
        try:
            out, residual = sweep_residual_placeholders(out)
            if residual:
                report = ctx.get("bilingual") or new_bilingual_report()
                report["residual_tokens"] = residual
                ctx["bilingual"] = report
        except Exception:
            logger.exception("residual placeholder sweep failed - cases unchanged")
    return out


# Post-generation, deterministic CONTENT enforcement.
#
# Bilingual: substitute ``{{EN:KEY}}`` / ``{{AR:KEY}}`` with the ticket's
# documented strings (the model is never asked to echo them), then run the
# residual-token sweep.
#
# CALL ORDER MATTERS:
#   * The substituted strings are UNTRUSTED ticket text, and
#     ``_rewrite_vague_fields`` (which runs after dedup) feeds step text to
#     ``ask_json``. That call site therefore carries ``_GUARD`` and wraps its
#     items -- see the agent edit in ops-b3-rule-packs.json.
#
# The residual sweep runs OUTSIDE the substitution try/except, so an exception
# inside substitution can never let a raw ``{{EN:DM01}}`` reach the tester.
#
# Content only -- the assumption Notes are built separately by
# ``rule_pack_notes`` because they are keyed by tc_id and the pipeline
# renumbers TC-001..N after this point.
#
# Returns ``(cases, ctx)``. Never raises.
def apply_rule_packs(
    cases: list[TestCase], result: RulePackResult
) -> tuple[list[TestCase], dict]:
    """Post-generation, deterministic CONTENT enforcement (substitute + sweep)."""
    ctx: dict = {
        "bilingual": new_bilingual_report(len(result.pairs)),
        "notes": {},
    }
    out = list(cases or [])
    if not result.active or not out:
        return out, ctx
    out = _substitute(cases, result, ctx)
    return _sweep(out, result, ctx), ctx


def _next_tc_id(cases: list[TestCase]) -> int:
    """One past the highest numeric id, clamped to the ``^TC-\\d{3,6}$`` pattern."""
    highest = 0
    for tc in cases:
        digits = "".join(ch for ch in (tc.tc_id or "") if ch.isdigit())
        if digits:
            highest = max(highest, int(digits))
    return min(max(highest + 1, 1), 999999)


# A no-op unless the bilingual pack is ON and the ticket documents pairs.
# Placed AFTER semantic dedup and AFTER ``_rewrite_vague_fields`` in the
# pipeline so neither can merge it away nor rewrite its fixed wording. The
# tc_id is one past the highest existing numeric id (clamped to the
# ``^TC-\d{3,6}$`` model pattern); the pipeline's final renumber rewrites it
# anyway. Never raises -- on failure the list is returned unchanged.
def inject_manual_validation_case(
    cases: list[TestCase], result: RulePackResult
) -> list[TestCase]:
    """Append the templated native-speaker linguistic-validation case."""
    try:
        if not result.bilingual_on or not result.pairs or not cases:
            return cases
        tc_id = f"TC-{_next_tc_id(cases):03d}"
        extra = build_manual_validation_case(result.pairs, tc_id=tc_id)
        if extra is None:
            return cases
        return list(cases) + [extra]
    except Exception:
        logger.exception("inject_manual_validation_case failed - suite unchanged")
        return cases
