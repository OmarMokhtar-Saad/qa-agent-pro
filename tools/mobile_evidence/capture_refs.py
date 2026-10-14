"""The reference objects of the per-case app-log lane.

``begin_case`` and ``slice_case`` take a :class:`CaseRef` (which case, on which
device and app), ``pull_events`` a :class:`RunRef` (which run), and
``slice_case`` a :class:`SliceCtx` (the begin record and the tester's typed
values). ``capture`` re-exports all three.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RunRef:
    serial: str
    package: str
    run_id: str


@dataclass(frozen=True)
class CaseRef:
    serial: str
    package: str
    run_id: str
    tc_id: str


@dataclass(frozen=True)
class SliceCtx:
    begin: dict | None
    tester_inputs: dict | None = None
