"""Request objects for the category-gate calls the host submit flow makes.

Types only (typing import): the callers live in agents/host_submit_*, which
cannot import handlers, so the shapes sit here and handlers import them."""

from __future__ import annotations

from typing import NamedTuple


class VolumeGateIn(NamedTuple):
    prep_id: str
    envelope: dict
    all_cases: list
    staged_rows: int
    ack: bool


class AssertionGateIn(NamedTuple):
    prep_id: str
    envelope: dict
    all_cases: list
    findings: list
    ack: bool


class EarlyNotesIn(NamedTuple):
    meta: dict
    dup_note: str
    source_text: str
    provenance: dict
    suite: object


class StagedHintIn(NamedTuple):
    prep_id: str
    extra_field: str = ""
    field_shape: str = ""
    staged_count: int | None = None
