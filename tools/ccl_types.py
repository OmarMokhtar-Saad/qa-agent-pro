"""Types for the coverage-class detector."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


def _rx(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.IGNORECASE)


@dataclass(frozen=True)
class CoverageClass:
    """One class of test a senior manual tester expects to find."""

    class_id: str
    label: str
    subject: re.Pattern
    adversity: re.Pattern | None
    floor: int = 1
    unspecified: bool = False


@dataclass
class ClassFinding:
    """What the suite actually contains for one class."""

    class_id: str
    label: str
    floor: int
    subject_hits: list[str] = field(default_factory=list)
    qualifying: list[str] = field(default_factory=list)
    unspecified: bool = False
