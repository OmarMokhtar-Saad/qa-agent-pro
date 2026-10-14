"""Parameter objects for the case-card builders."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class _HeadParts:
    """What a card's summary row takes besides the facts."""

    snippet: str
    attrs: str
    bits: str


@dataclass(frozen=True)
class _BodyParts:
    """The three pieces a card's body is built from besides the facts."""

    expected: str
    steps: str
    tech: str
