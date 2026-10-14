"""Rule-pack orchestrator (Batch 3): the single seam the generation agent uses.

The three rule packs (EN/AR bilingual, atomicity, standing API/UI) are rules about
WHAT MUST APPEAR ON THE ATOMIC REQUIREMENTS CHECKLIST, not pipeline stages:

    packs   = build_rule_packs(PackInputs(...))            # before the fan-out
    items   = rule_pack_checklist_items(packs)             # -> Batch-2 items
    block   = format_rule_pack_prompt_block(packs, True)   # appended to rtm_hint
    cases,c = apply_rule_packs(cases, packs)               # BEFORE semantic dedup
    keep    = protected_stable_ids(c)                      # -> dedup do-not-merge
    notes   = rule_pack_notes(final_cases, packs)          # after the renumber
    section = rule_pack_section(packs, final_cases, c)

Every function is never-raise; with all three flags OFF the result is inert.
Full design prose: tools/rule_packs_model.py. Facade over rule_packs_{model,build,
items,prompt,apply}.
"""

from __future__ import annotations

import logging

from tools.rule_packs_apply import apply_rule_packs, inject_manual_validation_case
from tools.rule_packs_build import run_enabled_packs
from tools.rule_packs_items import (
    line_subsystem_map,
    rule_pack_checklist_items,
    rule_pack_checklist_items_by_provenance,
)
from tools.rule_packs_model import (
    PackInputs,
    RulePackLine,
    RulePackResult,
)
from tools.rule_packs_prompt import (
    format_rule_pack_prompt_block,
    protected_stable_ids,
    rule_pack_notes,
    rule_pack_section,
)

logger = logging.getLogger(__name__)

__all__ = [
    "PackInputs",
    "RulePackLine",
    "RulePackResult",
    "apply_rule_packs",
    "atomicity_rules_enabled",
    "bilingual_rules_enabled",
    "build_rule_packs",
    "format_rule_pack_prompt_block",
    "inject_manual_validation_case",
    "line_subsystem_map",
    "protected_stable_ids",
    "rule_pack_checklist_items",
    "rule_pack_checklist_items_by_provenance",
    "rule_pack_notes",
    "rule_pack_section",
    "standing_rules_enabled",
]


# The EN/AR bilingual pack. HARDCODED OFF since 2026-08-14.
#
# NOT settings-derived: QA_BILINGUAL_RULES was DELETED (flag-surface
# reduction, batch 8b) and hardcoded to its own code default. A named seam
# rather than an inline literal so the whole pack below -- pair extraction,
# the mandated RP-I18N lines, the {{EN:..}}/{{AR:..}} substitution and the
# RTL-safe XLSX path -- stays executable by its existing tests under the
# tools/ >=90% floor, and so a revival is ONE line here.
def bilingual_rules_enabled() -> bool:
    """The EN/AR bilingual pack. HARDCODED OFF since 2026-08-14."""
    return False


# The anti-bundling pack. HARDCODED OFF since 2026-08-14.
#
# NOT settings-derived: QA_ATOMICITY_RULES was DELETED (flag-surface
# reduction, batch 8b) and hardcoded to its own code default.
#
# 2026-08-30 (audit G3): unlike its two siblings this seam no longer gates any
# CODE -- ``tools/atomicity.py`` was deleted, because a pack whose every call
# site is unreachable is dead code that only looks revivable. It is retained
# because ``RulePackResult.atomicity_on`` is part of the serialised prep
# envelope (``agents/host_*.py``) and because a True here must still not
# make the pack claim to run.
def atomicity_rules_enabled() -> bool:
    """The anti-bundling pack. HARDCODED OFF since 2026-08-14."""
    return False


# The standing API/UI pack. HARDCODED OFF since 2026-08-14.
#
# NOT settings-derived: QA_STANDING_RULES was DELETED (flag-surface
# reduction, batch 8b) and hardcoded to its own code default. Same seam
# rationale as bilingual_rules_enabled above.
def standing_rules_enabled() -> bool:
    """The standing API/UI pack. HARDCODED OFF since 2026-08-14."""
    return False


# ``jira_text`` is the stripped ticket body (description + acceptance criteria
# + comments) -- the EN/AR table lives there, not in the short ``feature_text``
# title. Never raises: any failure yields an inert result and the pipeline is
# unchanged.
def build_rule_packs(src: PackInputs) -> RulePackResult:
    """Run the enabled packs over the ticket content, BEFORE the fan-out."""
    result = RulePackResult(
        source_ref=src.source_ref,
        bilingual_on=bilingual_rules_enabled(),
        atomicity_on=atomicity_rules_enabled(),
        standing_on=standing_rules_enabled(),
    )
    if not result.active:
        return result
    try:
        run_enabled_packs(result, src)
    except Exception:
        logger.exception("build_rule_packs failed - rule packs are inert this run")
    return result
