"""Parameter objects of the report widgets: tiles, chips, section blocks and heads, page data, run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class _Kpi:
    """One KPI tile; ``value`` and ``detail`` arrive as markup already built through ``esc``."""

    value: str
    label: str
    detail: str = ""
    tone: str = ""
    hero: bool = False


@dataclass(frozen=True)
class _Chip:
    """One filter chip: its group, value, label, shown number and optional swatch class."""

    group: str
    value: str
    label: str
    num: int
    sw: str = ""


@dataclass(frozen=True)
class _SecBlock:
    """One collapsible section: title, body markup, an optional number and note."""

    title: str
    body: str
    num: Any = None
    note: str = ""
    open_: bool = False


@dataclass(frozen=True)
class _Head:
    """A section head; ``lede`` is markup built through ``esc``, ``level`` 3 draws an h3."""

    title: str
    label: str
    lede: str = ""
    level: int = 2


@dataclass(frozen=True)
class _PageData:
    """What the evidence builders read together, so each takes one argument.

    Every field is optional: a builder that needs only some of them (the facts
    strip wants ``loaded`` and ``coverage``) passes the rest at their defaults.
    """

    library: object = None
    app: str = ""
    loaded: dict | None = None
    coverage: dict | None = None
    media_map: object = None


@dataclass(frozen=True)
class _Run:
    """What every section of one page is built from, read ONCE."""

    run_id: str
    manifest: dict
    cases: list
    facts: list
    library: dict
    loaded: dict | None
    coverage: dict
