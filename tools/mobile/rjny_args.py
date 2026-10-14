"""Parameter objects for the journey and results builders."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class _Thumb:
    """What a film-strip thumbnail takes besides its facts."""

    n: int
    frame: dict
    library: object


@dataclass(frozen=True)
class _TurnIn:
    """A turn's number and the run manifest its headline is read from."""

    n: int
    manifest: dict


@dataclass(frozen=True)
class _JourneyData:
    """The run-wide lookups the journey is built from."""

    facts_by_id: dict
    cases_by_id: dict
    manifest: dict


@dataclass(frozen=True)
class _GoalSlot:
    """One goal: its position, its group, its turns' facts and the turn numbering."""

    gi: int
    group: dict
    facts: list
    order: dict


@dataclass(frozen=True)
class _ResultsIn:
    """What the header's result lines are built from."""

    groups: list
    scripted: list
    tally: dict
    total: int
