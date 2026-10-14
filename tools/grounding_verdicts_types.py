"""Verdict vocabulary and value types for the grounding review (leaf of grounding_verdicts)."""

from __future__ import annotations

from dataclasses import dataclass

from tools.models import TestCase

#: The three verdicts a host may return. Anything else is ignored.
ENTAILED = "entailed"
UNGROUNDED = "ungrounded"
UNSPECIFIED = "unspecified"
VALID_VERDICTS = frozenset({ENTAILED, UNGROUNDED, UNSPECIFIED})


@dataclass(frozen=True)
class Verdict:
    """One validated verdict about one case of the submitted suite."""

    tc_id: str
    verdict: str
    note: str = ""


@dataclass(frozen=True)
class Routing:
    """Outcome of applying verdicts to a suite.

    ``kept`` and ``routed`` partition the input, preserving order. ``refusal`` is
    a reviewer-facing reason when the batch was refused wholesale, in which case
    ``routed`` is empty and ``kept`` is the input unchanged.
    """

    kept: list[TestCase]
    routed: list[TestCase]
    refusal: str = ""

    @property
    def applied(self) -> bool:
        return not self.refusal and bool(self.routed)


def _by_id(verdicts: list[Verdict]) -> dict[str, Verdict]:
    return {v.tc_id: v for v in verdicts or []}


def _tc_id(tc: object) -> str:
    return getattr(tc, "tc_id", "") or ""
