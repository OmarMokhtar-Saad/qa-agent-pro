"""Value types for the run-level knowledge facts (see ``knowledge_run``)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import NamedTuple

STATES = ("unlearned", "learning", "learned", "skipped", "failed")


class RunKey(NamedTuple):
    """Which run of which app a ``runs`` row belongs to."""

    package: str
    run_id: str


class Move(NamedTuple):
    """The learn state a run moves to, with the error text that goes with it."""

    state: str
    error: str = ""


@dataclass
class RunFacts:
    run_id: str = ""
    package: str = ""
    app_version: str = ""
    version_code: str = ""
    env: dict = field(default_factory=dict)
    lane: str = ""
    knowledge_off: str = ""  # why knowledge is off for this run (C10), else ""
