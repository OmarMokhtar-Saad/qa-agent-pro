"""The generation categories and the Edge Cases retype."""

from __future__ import annotations

from dataclasses import dataclass, field

from tools.models import TestCase


@dataclass
class CategoryResult:
    category_name: str
    cases: list[TestCase] = field(default_factory=list)
    error: Exception | None = None
    attempts: int = 0

    @property
    def succeeded(self) -> bool:
        return self.error is None


# Each entry: (category_name, what_to_cover, preferred_type_value)
CATEGORIES: list[tuple[str, str, str]] = [
    (
        "Positive / Happy Path",
        "valid inputs, correct credentials, successful user journeys end-to-end",
        "Functional",
    ),
    (
        "Negative / Error Flows",
        "invalid inputs, missing required fields, wrong formats, rejection scenarios, "
        "error messages, and FAILURE OF A PROMISED SIDE EFFECT: where the source "
        "promises something downstream happens (a notification is sent, a record is "
        "written, a system is told), test what the user sees when that does NOT happen",
        "Negative",
    ),
    (
        "Boundary Values",
        "minimum, maximum, empty, null, zero, max-length+1 for every input field, and "
        "ENVIRONMENT boundaries: where the source pins a behaviour to one specific "
        "clock, timezone, locale or region, test that boundary from an environment "
        "that does NOT match the pinned reference",
        "Boundary",
    ),
    (
        "Edge Cases",
        "special characters, unicode, extremely long strings, concurrent actions, race "
        "conditions -- including an action that lands while a related operation is "
        "ALREADY IN FLIGHT (not merely two users editing the same field), and the "
        "SAME state-changing request submitted twice, where the case must assert how "
        "MANY times the effect was applied",
        "Exploratory",
    ),
    (
        "State Transitions",
        "session expiry, locked accounts, first-time users, account state changes, multi-step flows",
        "Functional",
    ),
    (
        "Security",
        "authentication bypass, brute force, SQL injection, XSS, unauthorised access, sensitive data exposure",
        "Security",
    ),
    (
        "UI/UX Validation",
        "error messages, button states, field validation feedback, loading states, "
        "empty states, and accessibility AND localization in DEPTH rather than one "
        "token case each -- screen-reader labels, focus order, contrast and text "
        "scaling; and where the product ships more than one language or script, the "
        "source's own quoted strings rendered in each",
        "Functional",
    ),
    (
        "Integration",
        "dependencies on other modules, APIs, third-party services, data persistence, "
        "event triggers, and what the tester observes when a dependency is "
        "unavailable or an event is never delivered",
        "Integration",
    ),
]


# Index of "Edge Cases" in CATEGORIES, plus the retype applied to it.
# UNCONDITIONAL since 2026-08-12 (QA_EDGE_CASES_FUNCTIONAL_TYPE was deleted).
# See config/settings.py for the measurement; the short version is that
# CATEGORIES[3] used to ask the model for type "Exploratory" while the cases it
# produces are fully scripted, which skewed the XLSX Summary's type metrics.
_EDGE_CASES_INDEX = 3


_EDGE_CASES_SCRIPTED_NOTE = (
    ' (these are SCRIPTED cases -- reserve type "Exploratory" for genuinely '
    "unscripted charters)"
)


def effective_categories() -> list[tuple[str, str, str]]:
    """CATEGORIES with the Edge Cases retype applied. Pure and never raises.

    Always returns a NEW list: the retype is unconditional since 2026-08-12.
    (A fixture directory recording the 8 category system prompts verbatim was
    re-captured against it at the time; that fixture set no longer exists in
    the repo, so nothing pins the prompts byte-for-byte today.) The
    module-level CATEGORIES list is never mutated. Read by BOTH halves: the server fan-out, and
    prepared.categories, which is what host mode builds its per-category
    instructions from.
    """
    out = list(CATEGORIES)
    name, focus, _ptype = out[_EDGE_CASES_INDEX]
    out[_EDGE_CASES_INDEX] = (name, focus + _EDGE_CASES_SCRIPTED_NOTE, "Functional")
    return out
