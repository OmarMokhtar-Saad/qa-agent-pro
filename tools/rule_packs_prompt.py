"""Rule-pack prompt block, assumption notes, advisory section and protected ids."""

from __future__ import annotations

import logging

from tools.bilingual import (
    bilingual_warning_section,
    format_bilingual_prompt_block,
)
from tools.bilingual import new_report as new_bilingual_report
from tools.models import TestCase
from tools.rule_packs_model import RulePackResult
from tools.standing_rules import (
    ContractBasis,
    annotate_assumed_cases,
    format_standing_prompt_block,
    standing_warning_section,
)

logger = logging.getLogger("tools.rule_packs")


def _prompt_parts(result: RulePackResult, mode: bool) -> list[str]:
    parts: list[str] = []
    if result.bilingual_on and result.pairs:
        parts.append(format_bilingual_prompt_block(result.pairs, checklist_mode=mode))
    if result.standing_on:
        parts.append(
            format_standing_prompt_block(
                result.triggers, result.source_ref, checklist_mode=mode
            )
        )
    return parts


# Contains ONLY code constants, opaque message keys and the sanitised source
# reference -- never untrusted ticket text, so it needs no ``wrap_untrusted``
# boundary and adds no untrusted block to the user message.
#
# ``checklist_mode`` (defaulting to ``result.checklist_mode``) makes the
# wording consistent with Batch 2's checklist hint: the packs point at the
# RP-* checklist ids and repeat that coverage is recomputed externally, instead
# of issuing a competing "produce a case for each bullet in addition to your
# category's normal output" mandate. Never raises.
def format_rule_pack_prompt_block(
    result: RulePackResult, checklist_mode: bool | None = None
) -> str:
    """The single system-prompt block for every enabled pack. "" when inert."""
    try:
        if not result.active:
            return ""
        mode = result.checklist_mode if checklist_mode is None else bool(checklist_mode)
        return "".join(p for p in _prompt_parts(result, mode) if p)
    except Exception:
        logger.exception("format_rule_pack_prompt_block failed - returning ''")
        return ""


def protected_stable_ids(ctx: dict | None) -> set[str]:
    """``stable_id``s of the bilingual cases that carry a documented pair.

    Two bilingual cases differ only by which documented message they quote,
    so any near-duplicate merge must treat this set as a do-not-merge list:
    merging either case destroys a MANDATED per-key case.

    Returns an empty set when the bilingual pack is off or nothing was
    substituted, which is the default path. Never raises.
    """
    try:
        report = (ctx or {}).get("bilingual") or {}
        return {str(sid) for sid in (report.get("protected_stable_ids") or []) if sid}
    except Exception:
        logger.exception("protected_stable_ids failed - protecting nothing")
        return set()


def _contract_basis(result: RulePackResult) -> ContractBasis:
    """The spec / bilingual basis the assumption label is built from."""
    return ContractBasis(
        result.triggers.has_spec,
        bilingual=bool(result.bilingual_on and result.pairs),
    )


# MUST be called on the FINAL, renumbered cases: the mapping is keyed by the
# display tc_id, which the pipeline reassigns TC-001..N in risk order after
# ``apply_rule_packs`` has run. Returns {} when the standing pack is off or
# nothing is marked. Never raises.
def rule_pack_notes(cases: list[TestCase], result: RulePackResult) -> dict[str, str]:
    """``{tc_id: note}`` for the XLSX Notes column."""
    try:
        if not result.standing_on or not result.triggers.fired or not cases:
            return {}
        return annotate_assumed_cases(
            cases,
            result.source_ref,
            _contract_basis(result),
        )
    except Exception:
        logger.exception("rule_pack_notes failed - no notes attached")
        return {}


def _section_parts(result: RulePackResult, ctx: dict) -> list[str]:
    parts: list[str] = []
    if result.bilingual_on and result.pairs:
        parts.append(
            bilingual_warning_section(
                result.pairs, ctx.get("bilingual") or new_bilingual_report()
            )
        )
    if result.standing_on:
        parts.append(
            standing_warning_section(
                result.triggers, ctx.get("notes") or {}, result.source_ref
            )
        )
    return parts


# Only the textual ``detect_bundled_cases`` signal runs; there is no
# external check behind it. Never raises.
def rule_pack_section(
    result: RulePackResult,
    cases: list[TestCase],
    ctx: dict | None = None,
) -> str:
    """Combined advisory markdown for every enabled pack. "" when inert."""
    try:
        if not result.active or not cases:
            return ""
        parts = _section_parts(result, ctx or {})
        return "".join(p for p in parts if p)
    except Exception:
        logger.exception("rule_pack_section failed - returning ''")
        return ""
