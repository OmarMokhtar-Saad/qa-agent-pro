"""The value types the category flow passes between its steps."""

from __future__ import annotations

from typing import Any, NamedTuple


class _CategoryArgs(NamedTuple):
    """The host's ids after the entry gates: sanitised, canonical, echo-safe."""

    prep_id: str
    category_name: str
    display: str


class _CategoryIntake(NamedTuple):
    """What a category submission looked like BEFORE anything was written."""

    parsed: Any
    cases_json: list
    meta_raw: Any
    meta: dict
    prior: int
    overridden: bool


class _CategoryStaged(NamedTuple):
    """What the write left behind: the post-write rows, their count, the client."""

    rows: dict
    on_file: int
    provenance: dict


class _StagedView(NamedTuple):
    """The staged-category count, its name suffix and the unrecognised warning."""

    on_file: int
    suffix: str
    warning: str


class _ReplyParts(NamedTuple):
    """The pieces of the success reply besides the ids: count, lead notes, route."""

    case_count: int
    lead: str
    tail: str
