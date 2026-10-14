"""Parameter objects of the knowledge db write path (``knowledge_db`` facade)."""

from __future__ import annotations

from typing import Any, NamedTuple


class Where(NamedTuple):
    """A constant SQL fragment with ``?`` placeholders, its bound args and a limit."""

    clause: str = ""
    args: Any = ()
    limit: int = 100


class Write(NamedTuple):
    values: Any
    run_id: Any = ""
    why: Any = ""


class Counter(NamedTuple):
    key: dict
    deltas: dict
    run_id: Any = ""
    why: Any = ""


class Replace(NamedTuple):
    old_id: int
    values: Any
    run_id: Any = ""
    why: Any = ""


class Status(NamedTuple):
    row_id: int
    status: str
    cols: Any = None
    run_id: Any = ""
    why: Any = ""


class Ev(NamedTuple):
    """One ``events`` row: the table/row it concerns and what undoes it."""

    table: str
    row_id: Any
    run_id: Any
    why: Any
    before: Any


class Refusal(NamedTuple):
    run_id: Any
    why: Any
    reason: str


class Retire(NamedTuple):
    closed: str
    stamp: str
    cutoff: float


class Hit(NamedTuple):
    """An open or old row plus the column set of its table."""

    table: str
    row: Any
    cols: frozenset = frozenset()
