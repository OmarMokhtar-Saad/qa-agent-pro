"""Rule-pack data model: result/line records, EARS/provenance tags, PackInputs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import NamedTuple

from tools.bilingual import LanguagePair
from tools.standing_rules import Triggers

# Rule-pack orchestrator (Batch 3) -- module prose carried verbatim from the
# old tools/rule_packs.py docstring:
#
# The three Batch 3 rule packs -- EN/AR bilingual, atomicity/anti-bundling and the
# standing API/UI rules -- are all rules about WHAT MUST APPEAR ON THE ATOMIC
# REQUIREMENTS CHECKLIST, not new pipeline stages. This module is the single seam
# the generation agent talks to, so each future rule costs one prompt clause and
# one advisory line instead of one pipeline stage:
#
#     packs   = build_rule_packs(...)                        # before the fan-out
#     items   = rule_pack_checklist_items(packs)             # -> Batch-2 items
#     block   = format_rule_pack_prompt_block(packs, True)   # appended to rtm_hint
#     cases,c = apply_rule_packs(cases, packs)               # BEFORE semantic dedup
#     keep    = protected_stable_ids(c)                      # -> dedup do-not-merge
#     notes   = rule_pack_notes(final_cases, packs)          # after the renumber
#     section = rule_pack_section(packs, final_cases, c)
#
# COMPOSITION WITH BATCH 2 IS A HARD DEPENDENCY, NOT A BRIDGE.
# ``rule_pack_checklist_items`` constructs REAL ``tools.atomic_checklist.ChecklistItem``
# instances (a pydantic BaseModel with ``model_config = {"extra": "forbid"}`` and
# exactly four fields: ``item_id`` / ``text`` / ``ears_pattern`` / ``source`` --
# tools/atomic_checklist.py:233-242). There is no ``register_mandatory_lines`` in
# Batch 2 and none is invented here: the agent APPENDS the returned items to its
# ``checklist_items`` list, so Batch 2's own
# ``format_checklist_prompt_block`` presents them and the 'Requirements
# Checklist' XLSX sheet renders them, with no Batch-2 code change at all.
#
# Because ``ChecklistItem`` forbids extra fields, the rule-pack metadata that has
# no home on it (``origin`` / ``subsystem``) stays on ``RulePackResult.lines`` and
# is joined back by ``line_id`` -- that is what ``line_subsystem_map`` is for.
#
# DEGRADATION WHEN THE CHECKLIST IS OFF. When the checklist is off -- since
# 2026-08-14 only under a revived ``tools/atomic_checklist.checklist_enabled``
# seam, the flag QA_ATOMIC_CHECKLIST_ENABLED having been deleted -- the agent's
# ``checklist_items`` is empty; the agent deliberately does NOT
# create a synthetic checklist out of rule-pack lines alone (that would silently
# switch the pipeline into checklist mode and skip ``qa_ac_anchoring_enforce``).
# The packs then run in PROMPT + ADVISORY mode: the rules still reach the
# generator, substitution and the advisory sections still run, but there is no
# external check enforcing them -- only the textual
# ``detect_bundled_cases`` signal. This is stated plainly in
# plan-b3-rule-packs.md -- no over-claiming.
#
# Every function is never-raise. With all three flags OFF, ``build_rule_packs``
# returns an inert result, every helper returns "" / the input unchanged, and the
# pipeline is byte-identical to today.
#
# 2026-08-30 (audit G3): the ATOMICITY pack's module, ``tools/atomicity.py``, was
# DELETED. It had been behaviourally unreachable since 2026-08-14 -- every call
# site sat under ``result.atomicity_on``, which ``build_rule_packs`` sets from
# ``atomicity_rules_enabled()``, the ``False`` constant. The seam and the
# ``RulePackResult.atomicity_on`` FIELD are retained: ``agents/host_*.py``
# serialises that field into the prep envelope, so removing it would be a
# prep-format change. What is gone is the split-rule instruction and the two
# bundling detectors; reviving them is a fresh implementation, not a flag flip.

# EARS pattern tags used for the mandated lines. Both are members of
# tools.atomic_checklist.EARS_PATTERNS, so Batch 2's renderers and its
# pattern-distribution audit treat them like any other line.
_EARS_EVENT = "event_driven"
_EARS_UBIQUITOUS = "ubiquitous"

# Provenance tags. Both match tools.atomic_checklist._SOURCE_PATTERNS, so they
# survive that module's ``normalize_source`` allowlist and do NOT drag the
# granularity audit's provenance ratio down. "description" for the bilingual
# lines (the EN/AR table is documented ticket text); "implied" for the standing
# rules (nothing on the ticket states them -- they are our domain policy).
_SOURCE_DOCUMENTED = "description"
_SOURCE_IMPLIED = "implied"


@dataclass(frozen=True)
class RulePackLine:
    """One mandated checklist line produced by a rule pack."""

    line_id: str
    text: str
    origin: str  # "bilingual" | "standing_api" | "standing_ui"
    subsystem: str  # "i18n" | "backend" | "ui"
    ears_pattern: str = _EARS_UBIQUITOUS
    source: str = _SOURCE_IMPLIED


@dataclass
class RulePackResult:
    """Everything the packs decided, computed once before the fan-out."""

    lines: list[RulePackLine] = field(default_factory=list)
    pairs: list[LanguagePair] = field(default_factory=list)
    triggers: Triggers = field(default_factory=Triggers)
    source_ref: str = ""
    bilingual_on: bool = False
    atomicity_on: bool = False
    standing_on: bool = False
    checklist_mode: bool = False

    @property
    def active(self) -> bool:
        return bool(self.bilingual_on or self.atomicity_on or self.standing_on)


class PackInputs(NamedTuple):
    """The ticket content ``build_rule_packs`` runs the enabled packs over."""

    feature_text: str
    jira_text: str = ""
    ui_content: dict | None = None
    openapi_text: str = ""
    images_present: bool = False
    source_ref: str = ""
