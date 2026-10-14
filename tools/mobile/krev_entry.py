"""One report item for the review and learned providers, shared by both."""

from __future__ import annotations

from typing import Any, NamedTuple


class Entry(NamedTuple):
    """The text of one item: its title, detail and the action hint (may be empty)."""

    title: str
    detail: str
    hint: str = ""


def _item(table: str, ident: Any, entry: Entry) -> dict:
    return {
        "table": table,
        "id": str(ident),
        "title": entry.title,
        "detail": entry.detail,
        "action_hint": entry.hint,
    }


def confirm_hint(table: str, ident: object) -> str:
    """The ``confirm`` hint for a learned item, empty when it has no id."""
    if ident == "":
        return ""
    return "qa_mobile_knowledge action=confirm table=%s item_id=%s" % (table, ident)


def learned_item(table: str, ident: object, entry: Entry) -> dict:
    """A learned item: the hint comes from the table and id, not from *entry*."""
    return _item(table, ident, entry._replace(hint=confirm_hint(table, ident)))
