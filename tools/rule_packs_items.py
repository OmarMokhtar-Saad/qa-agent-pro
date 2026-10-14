"""Rule-pack composition with Batch 2: mandated lines as real checklist items."""

from __future__ import annotations

import logging

from tools.atomic_checklist import ChecklistItem
from tools.rule_packs_model import _SOURCE_DOCUMENTED, RulePackResult

logger = logging.getLogger("tools.rule_packs")

_MSG_NO_ATOMIC = (
    "tools.atomic_checklist is missing - Batch 3 rule packs require Batch 2 "
    "(tools.atomic_checklist.checklist_enabled) for checklist enforcement; "
    "running in prompt + advisory mode only"
)
_MSG_ITEMS_FAILED = (
    "rule_pack_checklist_items failed - the mandated lines will not join the "
    "checklist; the packs still run in prompt + advisory mode"
)


def _split_by_source(items: list, result: RulePackResult) -> tuple[list, list]:
    """Partition ``items`` into (documented, implied) by their line's source."""
    by_id = {line.line_id: line for line in result.lines}
    documented: list = []
    implied: list = []
    for it in items:
        line = by_id.get(it.item_id)
        is_doc = line is not None and line.source == _SOURCE_DOCUMENTED
        (documented if is_doc else implied).append(it)
    logger.info(
        "rule-pack lines by provenance: %d documented (interleaved), "
        "%d implied (appended last)",
        len(documented),
        len(implied),
    )
    return documented, implied


# The two halves must be given DIFFERENT priority against the prompt budget,
# and the distinction is already in the data:
#
# * ``_SOURCE_DOCUMENTED`` — a bilingual ``RP-I18N-*`` line is an EN/AR pair
#   lifted from a documented DM##/MSG## table. It is a real ticket
#   requirement and gets equal standing with the rest of them.
# * ``_SOURCE_IMPLIED`` — a standing ``RP-SR-*`` line is assumed API/UI
#   policy. It stays last: assumed coverage must never displace a documented
#   requirement.
#
# Anything with an unrecognised source is treated as implied (the
# conservative side -- it can only lose priority, never steal it).
# Never raises; returns ``([], [])`` on any failure.
def rule_pack_checklist_items_by_provenance(
    result: RulePackResult,
) -> tuple[list, list]:
    """Split the mandated lines into ``(documented, implied)``. Never raises."""
    try:
        items = rule_pack_checklist_items(result)
        if not items:
            return [], []
        return _split_by_source(items, result)
    except Exception:
        logger.exception("rule_pack_checklist_items_by_provenance failed")
        return [], []


def _to_items(result: RulePackResult) -> list:
    """Construct the real ``ChecklistItem``s."""
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


# Batch 2's ``ChecklistItem`` is a pydantic BaseModel with
# ``model_config = {"extra": "forbid"}`` and exactly four fields -- ``item_id``,
# ``text``, ``ears_pattern``, ``source`` (tools/atomic_checklist.py:233-242).
# Constructing it here (rather than inventing a Batch-2 registration hook that
# does not exist) is what makes the enforcement REAL: the agent appends the
# returned items to ``checklist_items`` before
# ``format_checklist_prompt_block`` runs, so they are presented to the
# generator and written to the 'Requirements Checklist' XLSX sheet.
#
# Returns [] when nothing was mandated or when ``tools.atomic_checklist`` is
# unavailable (Batch 2 not installed -- a configuration error the plan calls
# out as a hard prerequisite, logged loudly here rather than crashing
# generation). Never raises.
def rule_pack_checklist_items(result: RulePackResult) -> list:
    """The mandated lines as REAL ``tools.atomic_checklist.ChecklistItem``s."""
    try:
        if not result.lines:
            return []
        return _to_items(result)
    except ImportError:
        logger.error(_MSG_NO_ATOMIC)
        return []
    except Exception:
        logger.exception(_MSG_ITEMS_FAILED)
        return []


def line_subsystem_map(result: RulePackResult) -> dict[str, str]:
    """``{line_id: subsystem}``, since ``ChecklistItem`` forbids extra fields.

    ``subsystem`` has no home on the checklist item itself and must be joined
    back by id.
    """
    try:
        return {line.line_id: line.subsystem for line in result.lines}
    except (AttributeError, TypeError):  # pragma: no cover - defensive
        return {}
