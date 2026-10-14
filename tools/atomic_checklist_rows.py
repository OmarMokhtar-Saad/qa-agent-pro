"""Persistence and export helpers for an atomic checklist."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise
from tools.atomic_checklist_core import ChecklistItem

logger = logging.getLogger("tools.atomic_checklist")


def checklist_to_dicts(items: list[ChecklistItem]) -> list[dict]:
    """Serialise the checklist for suite_store persistence. Never raises."""
    with never_raise(
        logger,
        "checklist_to_dicts failed — returning an empty payload",
        level=logging.ERROR,
        exc_info=True,
    ):
        return [it.model_dump() for it in items or []]
    return []


def checklist_from_dicts(rows: list[dict]) -> list[ChecklistItem]:
    """Rehydrate a persisted checklist, skipping malformed rows. Never raises."""
    out: list[ChecklistItem] = []
    with never_raise(
        logger,
        "checklist_from_dicts failed — returning what was parsed",
        level=logging.ERROR,
        exc_info=True,
    ):
        for row in rows or []:
            with never_raise(
                logger, "skipping a malformed checklist row", exc_info=True
            ):
                out.append(ChecklistItem(**row))
    return out


def _sheet_rows(items: list[ChecklistItem]) -> list:
    if not items:
        return []
    rows: list = [["Req ID", "Requirement (EARS)", "Pattern", "Source"]]
    for it in items:
        rows.append([it.item_id, it.text, it.ears_pattern, it.source])
    return rows


def checklist_rows(items: list[ChecklistItem]) -> list:
    """Rows (header first) for the 'Requirements Checklist' XLSX sheet.

    A PLAIN LIST of the parsed EARS requirements. Deterministic
    requirement_id traceability (``tools.rtm.rtm_rows``) is the 'Requirements
    Traceability' sheet instead. Pure — cell sanitisation happens in
    tools/xlsx_generator. Returns [] when there is nothing to write. Never
    raises."""
    with never_raise(
        logger,
        "checklist_rows failed — omitting the sheet",
        level=logging.ERROR,
        exc_info=True,
    ):
        return _sheet_rows(items)
    return []


def _warning_head(audit: dict) -> list[str]:
    return [
        "\n\n## Requirements Decomposition (advisory)",
        "",
        "The atomic checklist scored "
        f"{audit.get('score', 0)} on the granularity check "
        f"({audit.get('item_count', 0)} items) — below the configured "
        "threshold. Read the checklist above with these caveats:",
    ]


def granularity_warning_section(audit: dict | None) -> str:
    """Advisory markdown for a decomposition that failed the granularity gate.

    Returns "" when the audit passed or is absent. Never raises."""
    with never_raise(
        logger,
        "granularity_warning_section failed — returning empty string",
        level=logging.ERROR,
        exc_info=True,
    ):
        if not audit or audit.get("passed", True):
            return ""
        warnings = audit.get("warnings") or []
        if not warnings:
            return ""
        lines = _warning_head(audit)
        lines += [f"- {w}" for w in warnings]
        return "\n".join(lines)
    return ""
