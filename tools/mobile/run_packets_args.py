"""Parameter objects for the run packet builders (PLR0913 fixes)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CaseRef:
    """Which case a planning packet is for, and how many escapes it has used."""

    run_id: str
    tc_id: str
    escapes: int = 0


@dataclass(frozen=True)
class TesterAsk:
    """One ask of the tester: a field name and the question, or a guard term."""

    field: str
    prompt: str
    run_id: str
    tc_id: str
    guard_term: str = ""


@dataclass(frozen=True)
class ExploreRef:
    """Which run an exploratory turn belongs to and what budget is left."""

    run_id: str
    remaining: object = None
