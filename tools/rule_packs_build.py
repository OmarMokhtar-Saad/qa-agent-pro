"""Rule-pack build step: run the enabled packs and collect their mandated lines."""

from __future__ import annotations

import logging

from tools.bilingual import checklist_lines as bilingual_checklist_lines
from tools.bilingual import extract_language_pairs
from tools.rule_packs_model import (
    _EARS_EVENT,
    _EARS_UBIQUITOUS,
    _SOURCE_DOCUMENTED,
    _SOURCE_IMPLIED,
    PackInputs,
    RulePackLine,
    RulePackResult,
)
from tools.standing_rules import (
    TriggerContext,
    detect_triggers,
    standing_checklist_lines,
)

logger = logging.getLogger("tools.rule_packs")


def _run_bilingual_pack(result: RulePackResult, blob: str) -> None:
    """Extract the EN/AR pairs from ``blob`` and add their mandated lines."""
    result.pairs = extract_language_pairs(blob)
    for pair, text in zip(result.pairs, bilingual_checklist_lines(result.pairs)):
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
    logger.info("bilingual rule pack: %d EN/AR pair(s) documented", len(result.pairs))


def _standing_line(line_id: str, text: str, subsystem: str) -> RulePackLine:
    """One standing API/UI mandated line."""
    return RulePackLine(
        line_id=f"RP-{line_id}",
        text=text,
        origin="standing_api" if subsystem == "backend" else "standing_ui",
        subsystem=subsystem,
        ears_pattern=_EARS_UBIQUITOUS,
        source=_SOURCE_IMPLIED,
    )


def _log_standing(result: RulePackResult) -> None:
    logger.info(
        "standing rule pack: api=%s (weak_only=%s) ui=%s spec=%s lines=%d",
        result.triggers.api,
        result.triggers.api_weak_only,
        result.triggers.ui,
        result.triggers.has_spec,
        len(result.lines),
    )


def _add_standing_lines(result: RulePackResult) -> None:
    """Add the standing API/UI lines for ``result.triggers``."""
    for line_id, text, subsystem in standing_checklist_lines(result.triggers):
        result.lines.append(_standing_line(line_id, text, subsystem))
    _log_standing(result)


def _detect(src: PackInputs):
    return detect_triggers(
        feature_text=src.feature_text,
        jira_text=src.jira_text,
        context=TriggerContext(
            ui_content=src.ui_content,
            openapi_text=src.openapi_text,
            images_present=src.images_present,
        ),
    )


def run_enabled_packs(result: RulePackResult, src: PackInputs) -> None:
    """Run the enabled packs over ``src``, filling ``result`` in place."""
    blob = "\n".join(t for t in (src.jira_text or "", src.feature_text or "") if t)
    if result.bilingual_on:
        _run_bilingual_pack(result, blob)
    if result.standing_on:
        result.triggers = _detect(src)
        _add_standing_lines(result)
