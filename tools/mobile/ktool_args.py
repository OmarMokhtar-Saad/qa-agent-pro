"""Parameter objects for the ``qa_mobile_knowledge`` tool (typing only)."""

from __future__ import annotations

from typing import Any, NamedTuple


class Ask(NamedTuple):
    """One ``qa_mobile_knowledge`` request; defaults are the old ``handle`` ones."""

    action: str = "list"
    package: str = ""
    item_id: Any = 0
    run_id: str = ""
    data: dict | None = None


class Retire(NamedTuple):
    package: str
    note_id: Any
    reason: str
    run_id: str


class Scope(NamedTuple):
    package: str
    run_id: str


class Ruling(NamedTuple):
    verdict: str
    prop: dict
    run_id: str


class Found(NamedTuple):
    """A disputed note row and the validated text of its proposal."""

    row: Any
    got: Any


class Edit(NamedTuple):
    column: str
    text: str
    run_id: str


class Raw(NamedTuple):
    table: str
    values: dict
    run_id: str


class Page(NamedTuple):
    window: list
    page: Any
    more: bool


class Parsed(NamedTuple):
    ask: Ask
    table: str
    extra: dict
