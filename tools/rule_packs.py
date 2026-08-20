"""Rule-pack orchestrator (Batch 3).

The three Batch 3 rule packs -- EN/AR bilingual, atomicity/anti-bundling and the
standing API/UI rules -- are all rules about WHAT MUST APPEAR ON THE ATOMIC
REQUIREMENTS CHECKLIST, not new pipeline stages. This module is the single seam
the generation agent talks to, so each future rule costs one prompt clause and
one advisory line instead of one pipeline stage:

    packs   = build_rule_packs(...)                        # before the fan-out
    items   = rule_pack_checklist_items(packs)             # -> Batch-2 items
    block   = format_rule_pack_prompt_block(packs, True)   # appended to rtm_hint
    cases,c = apply_rule_packs(cases, packs)               # BEFORE semantic dedup
    keep    = protected_stable_ids(c)                      # -> dedup do-not-merge
    notes   = rule_pack_notes(final_cases, packs)          # after the renumber
    section = rule_pack_section(packs, final_cases, c,
                               matches=coverage_matches(coverage))

COMPOSITION WITH BATCH 2 IS A HARD DEPENDENCY, NOT A BRIDGE.
``rule_pack_checklist_items`` constructs REAL ``tools.atomic_checklist.ChecklistItem``
instances (a pydantic BaseModel with ``model_config = {"extra": "forbid"}`` and
exactly four fields: ``item_id`` / ``text`` / ``ears_pattern`` / ``source`` --
tools/atomic_checklist.py:233-242). There is no ``register_mandatory_lines`` in
Batch 2 and none is invented here: the agent APPENDS the returned items to its
``checklist_items`` list, so Batch 2's own
``format_checklist_prompt_block`` presents them, its ``match_checklist`` scores
them and its coverage tally / XLSX sheets render them, with no Batch-2 code
change at all.

Because ``ChecklistItem`` forbids extra fields, the rule-pack metadata that has
no home on it (``origin`` / ``subsystem``) stays on ``RulePackResult.lines`` and
is joined back by ``line_id`` -- that is what ``line_subsystem_map`` is for.

DEGRADATION WHEN THE CHECKLIST IS OFF. When the checklist is off -- since
2026-08-14 only under a revived ``tools/atomic_checklist.checklist_enabled``
seam, the flag QA_ATOMIC_CHECKLIST_ENABLED having been deleted -- the agent's
``checklist_items`` is empty; the agent deliberately does NOT
create a synthetic checklist out of rule-pack lines alone (that would silently
switch the pipeline into checklist mode and skip ``qa_ac_anchoring_enforce``).
The packs then run in PROMPT + ADVISORY mode: the rules still reach the
generator, substitution and the advisory sections still run, but there is no
external coverage tally enforcing them. ``coverage_matches`` returns {} and the
checklist-driven bundling signal is simply absent. This is stated plainly in
plan-b3-rule-packs.md -- no over-claiming.

Every function is never-raise. With all three flags OFF, ``build_rule_packs``
returns an inert result, every helper returns "" / the input unchanged, and the
pipeline is byte-identical to today.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from tools.atomicity import (
    ATOMICITY_INSTRUCTION,
    bundling_warning_section,
    detect_bundled_cases,
    detect_cross_line_bundles,
)
from tools.bilingual import (
    LanguagePair,
    bilingual_warning_section,
    build_manual_validation_case,
    extract_language_pairs,
    format_bilingual_prompt_block,
    substitute_placeholders,
    sweep_residual_placeholders,
)
from tools.bilingual import checklist_lines as bilingual_checklist_lines
from tools.bilingual import new_report as new_bilingual_report
from tools.models import TestCase
from tools.standing_rules import (
    Triggers,
    annotate_assumed_cases,
    detect_triggers,
    format_standing_prompt_block,
    standing_checklist_lines,
    standing_warning_section,
)

logger = logging.getLogger(__name__)

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


def bilingual_rules_enabled() -> bool:
    """The EN/AR bilingual pack. HARDCODED OFF since 2026-08-14.

    NOT settings-derived: QA_BILINGUAL_RULES was DELETED (flag-surface
    reduction, batch 8b) and hardcoded to its own code default. A named seam
    rather than an inline literal so the whole pack below -- pair extraction,
    the mandated RP-I18N lines, the {{EN:..}}/{{AR:..}} substitution and the
    RTL-safe XLSX path -- stays executable by its existing tests under the
    tools/ >=90% floor, and so a revival is ONE line here.
    """
    return False


def atomicity_rules_enabled() -> bool:
    """The anti-bundling pack. HARDCODED OFF since 2026-08-14.

    NOT settings-derived: QA_ATOMICITY_RULES was DELETED (flag-surface
    reduction, batch 8b) and hardcoded to its own code default. Same seam
    rationale as bilingual_rules_enabled above.
    """
    return False


def standing_rules_enabled() -> bool:
    """The standing API/UI pack. HARDCODED OFF since 2026-08-14.

    NOT settings-derived: QA_STANDING_RULES was DELETED (flag-surface
    reduction, batch 8b) and hardcoded to its own code default. Same seam
    rationale as bilingual_rules_enabled above.
    """
    return False


def build_rule_packs(
    feature_text: str,
    jira_text: str = "",
    ui_content: dict | None = None,
    openapi_text: str = "",
    images_present: bool = False,
    source_ref: str = "",
) -> RulePackResult:
    """Run the enabled packs over the ticket content, BEFORE the fan-out.

    ``jira_text`` is the stripped ticket body (description + acceptance criteria
    + comments) -- the EN/AR table lives there, not in the short ``feature_text``
    title. Never raises: any failure yields an inert result and the pipeline is
    unchanged.
    """
    result = RulePackResult(
        source_ref=source_ref,
        bilingual_on=bilingual_rules_enabled(),
        atomicity_on=atomicity_rules_enabled(),
        standing_on=standing_rules_enabled(),
    )
    if not result.active:
        return result
    try:
        blob = "\n".join(t for t in (jira_text or "", feature_text or "") if t)
        if result.bilingual_on:
            result.pairs = extract_language_pairs(blob)
            for pair, text in zip(
                result.pairs, bilingual_checklist_lines(result.pairs)
            ):
                result.lines.append(
                    RulePackLine(
                        line_id=f"RP-I18N-{pair.key}",
                        text=text,
                        origin="bilingual",
                        subsystem="i18n",
                        ears_pattern=_EARS_EVENT,
                        source=_SOURCE_DOCUMENTED,
                    )
                )
            logger.info(
                "bilingual rule pack: %d EN/AR pair(s) documented", len(result.pairs)
            )
        if result.standing_on:
            result.triggers = detect_triggers(
                feature_text=feature_text,
                jira_text=jira_text,
                ui_content=ui_content,
                openapi_text=openapi_text,
                images_present=images_present,
            )
            for line_id, text, subsystem in standing_checklist_lines(result.triggers):
                result.lines.append(
                    RulePackLine(
                        line_id=f"RP-{line_id}",
                        text=text,
                        origin=(
                            "standing_api" if subsystem == "backend" else "standing_ui"
                        ),
                        subsystem=subsystem,
                        ears_pattern=_EARS_UBIQUITOUS,
                        source=_SOURCE_IMPLIED,
                    )
                )
            logger.info(
                "standing rule pack: api=%s (weak_only=%s) ui=%s spec=%s lines=%d",
                result.triggers.api,
                result.triggers.api_weak_only,
                result.triggers.ui,
                result.triggers.has_spec,
                len(result.lines),
            )
    except Exception:
        logger.exception("build_rule_packs failed - rule packs are inert this run")
    return result


# --- Batch 2 composition ------------------------------------------------------


def rule_pack_checklist_items_by_provenance(
    result: RulePackResult,
) -> tuple[list, list]:
    """Split the mandated lines into ``(documented, implied)``.

    The two halves must be given DIFFERENT priority against the prompt budget,
    and the distinction is already in the data:

    * ``_SOURCE_DOCUMENTED`` — a bilingual ``RP-I18N-*`` line is an EN/AR pair
      lifted from a documented DM##/MSG## table. It is a real ticket
      requirement and gets equal standing with the rest of them.
    * ``_SOURCE_IMPLIED`` — a standing ``RP-SR-*`` line is assumed API/UI
      policy. It stays last: assumed coverage must never displace a documented
      requirement.

    Anything with an unrecognised source is treated as implied (the
    conservative side -- it can only lose priority, never steal it).
    Never raises; returns ``([], [])`` on any failure.
    """
    try:
        items = rule_pack_checklist_items(result)
        if not items:
            return [], []
        by_id = {line.line_id: line for line in result.lines}
        documented, implied = [], []
        for it in items:
            line = by_id.get(it.item_id)
            target = (
                documented
                if line is not None and line.source == _SOURCE_DOCUMENTED
                else implied
            )
            target.append(it)
        logger.info(
            "rule-pack lines by provenance: %d documented (interleaved), "
            "%d implied (appended last)",
            len(documented),
            len(implied),
        )
        return documented, implied
    except Exception:
        logger.exception("rule_pack_checklist_items_by_provenance failed")
        return [], []


def rule_pack_checklist_items(result: RulePackResult) -> list:
    """The mandated lines as REAL ``tools.atomic_checklist.ChecklistItem``s.

    Batch 2's ``ChecklistItem`` is a pydantic BaseModel with
    ``model_config = {"extra": "forbid"}`` and exactly four fields -- ``item_id``,
    ``text``, ``ears_pattern``, ``source`` (tools/atomic_checklist.py:233-242).
    Constructing it here (rather than inventing a Batch-2 registration hook that
    does not exist) is what makes the enforcement REAL: the agent appends the
    returned items to ``checklist_items`` before
    ``format_checklist_prompt_block`` runs, so they are presented to the
    generator, scored by ``tools.rtm.match_checklist``, counted in the coverage
    tally and written to the 'Requirements Checklist' XLSX sheet.

    Returns [] when nothing was mandated or when ``tools.atomic_checklist`` is
    unavailable (Batch 2 not installed -- a configuration error the plan calls
    out as a hard prerequisite, logged loudly here rather than crashing
    generation). Never raises.
    """
    try:
        if not result.lines:
            return []
        from tools.atomic_checklist import ChecklistItem

        items = [
            ChecklistItem(
                item_id=line.line_id,
                text=line.text,
                ears_pattern=line.ears_pattern,
                source=line.source,
            )
            for line in result.lines
        ]
        logger.info(
            "rule packs mandated %d checklist line(s): %s",
            len(items),
            ", ".join(i.item_id for i in items[:10]),
        )
        return items
    except ImportError:
        logger.error(
            "tools.atomic_checklist is missing - Batch 3 rule packs require Batch 2 "
            "(tools.atomic_checklist.checklist_enabled) for checklist enforcement; "
            "running in prompt + advisory mode only"
        )
        return []
    except Exception:
        logger.exception(
            "rule_pack_checklist_items failed - the mandated lines will not join the "
            "checklist; the packs still run in prompt + advisory mode"
        )
        return []


def coverage_matches(coverage: object) -> dict[str, list[str]]:
    """Adapter: Batch-2 ``ChecklistCoverage`` -> ``{item_id: [tc_id, ...]}``.

    Batch 2's matcher returns a ``ChecklistCoverage`` dataclass whose ``links``
    are ``MatchLink(item_id, tc_id, score, confidence, tier)`` -- NOT a plain
    dict (tools/rtm.py, added by ops-b2-atomic-checklist.json). Duck-typed on
    purpose so this module never imports the matcher and cannot create a cycle.

    Returns {} for ``None``, for a coverage object that did not run, or on any
    failure -- which is exactly the prompt+advisory-mode behaviour. Never raises.
    """
    try:
        if coverage is None or not getattr(coverage, "ran", False):
            return {}
        out: dict[str, list[str]] = {}
        for link in getattr(coverage, "links", None) or []:
            item_id = str(getattr(link, "item_id", "") or "")
            tc_id = str(getattr(link, "tc_id", "") or "")
            if item_id and tc_id:
                out.setdefault(item_id, []).append(tc_id)
        return out
    except Exception:
        logger.exception("coverage_matches failed - returning no matches")
        return {}


def line_subsystem_map(result: RulePackResult) -> dict[str, str]:
    """``{line_id: subsystem}`` for the checklist-driven bundling signal.

    Exists because ``ChecklistItem`` forbids extra fields, so ``subsystem`` has
    no home on the checklist item itself and must be joined back by id.
    """
    try:
        return {line.line_id: line.subsystem for line in result.lines}
    except Exception:  # pragma: no cover - defensive
        return {}


# --- Prompt block -------------------------------------------------------------


def format_rule_pack_prompt_block(
    result: RulePackResult, checklist_mode: bool | None = None
) -> str:
    """The single system-prompt block for every enabled pack. "" when inert.

    Contains ONLY code constants, opaque message keys and the sanitised source
    reference -- never untrusted ticket text, so it needs no ``wrap_untrusted``
    boundary and adds no untrusted block to the user message.

    ``checklist_mode`` (defaulting to ``result.checklist_mode``) makes the
    wording consistent with Batch 2's checklist hint: the packs point at the
    RP-* checklist ids and repeat that coverage is recomputed externally, instead
    of issuing a competing "produce a case for each bullet in addition to your
    category's normal output" mandate. Never raises.
    """
    try:
        if not result.active:
            return ""
        mode = result.checklist_mode if checklist_mode is None else bool(checklist_mode)
        parts: list[str] = []
        if result.bilingual_on and result.pairs:
            parts.append(
                format_bilingual_prompt_block(result.pairs, checklist_mode=mode)
            )
        if result.atomicity_on:
            parts.append(ATOMICITY_INSTRUCTION)
        if result.standing_on:
            parts.append(
                format_standing_prompt_block(
                    result.triggers, result.source_ref, checklist_mode=mode
                )
            )
        return "".join(p for p in parts if p)
    except Exception:
        logger.exception("format_rule_pack_prompt_block failed - returning ''")
        return ""


# --- Post-generation ----------------------------------------------------------


def apply_rule_packs(
    cases: list[TestCase], result: RulePackResult
) -> tuple[list[TestCase], dict]:
    """Post-generation, deterministic CONTENT enforcement.

    Bilingual: substitute ``{{EN:KEY}}`` / ``{{AR:KEY}}`` with the ticket's
    documented strings (the model is never asked to echo them), then run the
    residual-token sweep.

    CALL ORDER MATTERS TWICE:
      * BEFORE ``_semantic_dedupe_cases``. Un-substituted bilingual cases are
        near-identical templated text differing only by an opaque key, so
        embedding cosine between them is very high and dedup would merge away
        mandated per-key coverage. Substituting first makes each case carry its
        own distinct message text.
      * The substituted strings are UNTRUSTED ticket text, and
        ``_rewrite_vague_fields`` (which runs after dedup) feeds step text to
        ``ask_json``. That call site therefore carries ``_GUARD`` and wraps its
        items -- see the agent edit in ops-b3-rule-packs.json.

    The residual sweep runs OUTSIDE the substitution try/except, so an exception
    inside substitution can never let a raw ``{{EN:DM01}}`` reach the tester.

    Content only -- the assumption Notes are built separately by
    ``rule_pack_notes`` because they are keyed by tc_id and the pipeline
    renumbers TC-001..N after this point.

    Returns ``(cases, ctx)``. Never raises.
    """
    ctx: dict = {
        "bilingual": new_bilingual_report(len(result.pairs)),
        "notes": {},
    }
    out = list(cases or [])
    if not result.active or not out:
        return out, ctx
    try:
        if result.bilingual_on and result.pairs:
            out, ctx["bilingual"] = substitute_placeholders(out, result.pairs)
    except Exception:
        logger.exception("apply_rule_packs substitution failed - cases unchanged")
        out = list(cases or [])
    if result.bilingual_on:
        try:
            out, residual = sweep_residual_placeholders(out)
            if residual:
                report = ctx.get("bilingual") or new_bilingual_report()
                report["residual_tokens"] = residual
                ctx["bilingual"] = report
        except Exception:
            logger.exception("residual placeholder sweep failed - cases unchanged")
    return out, ctx


def protected_stable_ids(ctx: dict | None) -> set[str]:
    """``stable_id``s that ``_semantic_dedupe_cases`` must never merge away.

    Ordering substitution BEFORE semantic dedup is necessary but NOT sufficient.
    Two bilingual cases still differ only by which documented message they quote
    ("the banner shows Login failed" vs. "the banner shows Account locked"), and a
    real sentence-embedding model can score that pair above
    ``QA_SEMANTIC_DEDUP_THRESHOLD`` (default 0.9). Merging either one destroys a
    MANDATED per-key checklist line, which then reports as an uncovered
    requirement -- the batch would be flagging a gap it created itself.

    So the agent threads this set into ``_semantic_dedupe_cases`` as a
    do-not-merge list. ``stable_id`` is derived from (title, steps) and the only
    mutation between substitution and the dedup call is the residual sweep's
    ``model_copy``, which does NOT re-run that validator -- so these ids still
    match at the call site.

    Returns an empty set when the bilingual pack is off or nothing was
    substituted, which is the byte-identical-to-today path. Never raises.
    """
    try:
        report = (ctx or {}).get("bilingual") or {}
        return {str(sid) for sid in (report.get("protected_stable_ids") or []) if sid}
    except Exception:
        logger.exception("protected_stable_ids failed - protecting nothing")
        return set()


def inject_manual_validation_case(
    cases: list[TestCase], result: RulePackResult
) -> list[TestCase]:
    """Append the templated native-speaker linguistic-validation case.

    A no-op unless the bilingual pack is ON and the ticket documents pairs.
    Placed AFTER semantic dedup and AFTER ``_rewrite_vague_fields`` in the
    pipeline so neither can merge it away nor rewrite its fixed wording. The
    tc_id is one past the highest existing numeric id (clamped to the
    ``^TC-\\d{3,6}$`` model pattern); the pipeline's final renumber rewrites it
    anyway. Never raises -- on failure the list is returned unchanged.
    """
    try:
        if not result.bilingual_on or not result.pairs or not cases:
            return cases
        highest = 0
        for tc in cases:
            digits = "".join(ch for ch in (tc.tc_id or "") if ch.isdigit())
            if digits:
                highest = max(highest, int(digits))
        next_id = min(max(highest + 1, 1), 999999)
        extra = build_manual_validation_case(result.pairs, tc_id=f"TC-{next_id:03d}")
        if extra is None:
            return cases
        return list(cases) + [extra]
    except Exception:
        logger.exception("inject_manual_validation_case failed - suite unchanged")
        return cases


def rule_pack_notes(cases: list[TestCase], result: RulePackResult) -> dict[str, str]:
    """``{tc_id: note}`` for the XLSX Notes column.

    MUST be called on the FINAL, renumbered cases: the mapping is keyed by the
    display tc_id, which the pipeline reassigns TC-001..N in risk order after
    ``apply_rule_packs`` has run. Returns {} when the standing pack is off or
    nothing is marked. Never raises.
    """
    try:
        if not result.standing_on or not result.triggers.fired or not cases:
            return {}
        return annotate_assumed_cases(
            cases,
            result.source_ref,
            result.triggers.has_spec,
            bilingual=bool(result.bilingual_on and result.pairs),
        )
    except Exception:
        logger.exception("rule_pack_notes failed - no notes attached")
        return {}


def rule_pack_section(
    result: RulePackResult,
    cases: list[TestCase],
    ctx: dict | None = None,
    matches: dict[str, list[str]] | None = None,
) -> str:
    """Combined advisory markdown for every enabled pack. "" when inert.

    ``matches`` is the Batch-2 backward-matcher output already adapted by
    ``coverage_matches``; when non-empty it powers the second, checklist-driven
    bundling signal. Empty in prompt+advisory mode, where only the textual
    ``detect_bundled_cases`` signal runs. Never raises.
    """
    try:
        if not result.active or not cases:
            return ""
        ctx = ctx or {}
        parts: list[str] = []
        if result.bilingual_on and result.pairs:
            parts.append(
                bilingual_warning_section(
                    result.pairs, ctx.get("bilingual") or new_bilingual_report()
                )
            )
        if result.atomicity_on:
            parts.append(
                bundling_warning_section(
                    detect_bundled_cases(cases),
                    detect_cross_line_bundles(
                        matches or {}, line_subsystem_map(result)
                    ),
                )
            )
        if result.standing_on:
            parts.append(
                standing_warning_section(
                    result.triggers, ctx.get("notes") or {}, result.source_ref
                )
            )
        return "".join(p for p in parts if p)
    except Exception:
        logger.exception("rule_pack_section failed - returning ''")
        return ""
