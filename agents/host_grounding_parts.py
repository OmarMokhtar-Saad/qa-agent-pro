"""Host mode grounding: parse, judge and render the host's entailment verdicts."""

from __future__ import annotations

import dataclasses
from types import ModuleType
from typing import Any, NamedTuple

import tools.grounding_verdicts as gv


@dataclasses.dataclass(frozen=True)
class RoutedCases:
    """Cases an entailment review moved off the suite, plus their export rows."""

    routed: list
    rows: list


class Judged(NamedTuple):
    cases: list
    verdicts: Any
    routing: Any


def _gv() -> ModuleType:
    """The verdict module, imported lazily (it owns every bound)."""

    return gv


def judge_verdicts(raw: object, cases: list) -> Judged | None:
    """Parse the host's verdicts against the submitted cases; None when none are usable."""
    gv = _gv()
    cs = list(cases or [])
    ids = [getattr(c, "tc_id", "") or "" for c in cs]
    verdicts = gv.parse_verdicts(raw, ids)
    if not verdicts:
        return None
    return Judged(cs, verdicts, gv.split_ungrounded(cs, verdicts))


def render_sections(found: Judged) -> str:
    """The reviewer-facing markdown sections of a judged submission."""
    gv = _gv()
    return (
        gv.refusal_section(found.routing)
        + gv.assumed_requirements_section(found.routing.routed, found.verdicts)
        + gv.unspecified_section(found.cases, found.verdicts)
    )


def routed_cases(found: Judged) -> RoutedCases | None:
    """The cases to move off the suite with their rows, or None when nothing moves."""
    if not found.routing.routed:
        return None
    return RoutedCases(
        routed=list(found.routing.routed),
        rows=_gv().assumed_requirements_rows(found.routing.routed, found.verdicts),
    )
