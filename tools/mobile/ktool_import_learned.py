"""``qa_mobile_knowledge`` import: checks for the learned (non-note) tables."""

from __future__ import annotations

import json

from tools.mobile import knowledge_plug_locators as plug_locators
from tools.mobile import knowledge_stage_popups as popups_stage
from tools.mobile.ktool_imp_rules import (
    _EVIDENCE,
    _EVIDENCE_NUMERIC,
    _JSON_SHAPES,
    _popup_consistent,
    _strict_dumps,
)


def _shapes_ok(table: str, values: dict) -> bool:
    for col, shape in _JSON_SHAPES.get(table, {}).items():
        if col not in values or col == "runs_json":
            continue
        raw = values[col]
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(parsed, shape):
                return False
            values[col] = _strict_dumps(parsed)
        except ValueError:
            return False
    return True


def _popup_ok(values: dict) -> bool:
    dismiss = json.loads(values.get("dismiss_json") or "{}")
    if not _popup_consistent(values, dismiss):
        return False
    return not popups_stage.looks_destructive(dismiss.get("rid"))


def _locator_queue(values: dict) -> None:
    # Per-locator hit counts are evidence too; only the locators carry over.
    queue = json.loads(values["locators_json"])
    values["locators_json"] = _strict_dumps(
        [
            {"l": e["l"], "h": 0, "m": 0}
            for e in queue
            if isinstance(e, dict)
            and isinstance(e.get("l"), str)
            and plug_locators.split_locator(e["l"])[0]
        ]
    )


def _import_learned(table: str, values: dict) -> bool:
    """Each JSON column must parse to its schema shape; a destructive-looking
    popup dismiss is refused, as the popups stage never records one."""
    if not _shapes_ok(table, values):
        return False
    if table == "popups" and not _popup_ok(values):
        return False
    if table == "elements" and "locators_json" in values:
        _locator_queue(values)
    values.update({col: v for col, v in _EVIDENCE.items() if col in values})
    for col in _EVIDENCE_NUMERIC:
        values.pop(col, None)
    return True
