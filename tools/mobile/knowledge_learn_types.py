"""Value types of the learning pipeline (see ``knowledge_learn``)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import NamedTuple

STAGES = (
    "timings",
    "locators",
    "popups",
    "edges",
    "shortcuts",
    "flows",
    "mistakes",
    "versions",
    "gate",
)

MARKER = "learn_marker"


@dataclass
class StageResult:
    stage: str
    wrote: int = 0
    notes: list = field(default_factory=list)
    error: str = ""


@dataclass
class LearnCtx:
    package: str
    run_id: str
    facts: object
    steps: list
    verdict: dict
    conn: object
    now: float


class Outcome(NamedTuple):
    """How one learn pass ended: the run's new state and what it produced."""

    state: str
    error: str
    summary: dict
