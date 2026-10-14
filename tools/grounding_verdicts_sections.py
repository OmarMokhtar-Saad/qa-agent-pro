"""Advisory markdown sections for grounding verdicts (leaf of grounding_verdicts)."""

from __future__ import annotations

import logging

from tools.grounding_verdicts_types import (
    UNSPECIFIED,
    Routing,
    Verdict,
    _by_id,
    _tc_id,
)
from tools.models import TestCase

logger = logging.getLogger("tools.grounding_verdicts")

_MAX_LISTED = 25


def _overflow_line(total: int) -> list[str]:
    return [f"- ... and {total - _MAX_LISTED} more"] if total > _MAX_LISTED else []


def _assumed_lines(routed: list[TestCase], notes: dict[str, Verdict]) -> list[str]:
    lines: list[str] = []
    for tc in routed[:_MAX_LISTED]:
        tc_id = _tc_id(tc) or "?"
        title = (getattr(tc, "title", "") or "")[:110]
        lines.append(f"- **{tc_id}** — {title}")
        verdict = notes.get(tc_id)
        note = (verdict.note if verdict else "") or ""
        if note:
            lines.append(f"    - assumed: {note}")
    return lines


def _assumed_head(n: int) -> list[str]:
    return [
        "\n\n## Assumed Requirements (confirm with the BA)",
        "",
        f"{n} case(s) assert behaviour the ticket does not state. They "
        "are NOT in the executable suite -- they are on the **Assumed "
        "Requirements** sheet of the export, with their own AR-nnn ids. Nothing "
        "was deleted: each is either a real requirement nobody wrote down, or a "
        "case to drop, and a human decides which:",
    ]


def assumed_requirements_section(
    routed: list[TestCase], verdicts: list[Verdict]
) -> str:
    """Advisory markdown for the cases moved off the executable suite."""
    try:
        if not routed:
            return ""
        lines = _assumed_head(len(routed)) + _assumed_lines(routed, _by_id(verdicts))
        return "\n".join(lines + _overflow_line(len(routed)))
    except Exception:
        logger.exception("assumed_requirements_section failed - returning empty string")
        return ""


def _unspec_lines(marked: dict[str, Verdict], titles: dict[str, str]) -> list[str]:
    lines: list[str] = []
    for tc_id in sorted(marked)[:_MAX_LISTED]:
        lines.append(f"- **{tc_id}** — {titles.get(tc_id, '')[:110]}")
        if marked[tc_id].note:
            lines.append(f"    - unspecified: {marked[tc_id].note}")
    return lines


def _unspec_head(n: int) -> list[str]:
    return [
        "\n\n## Unspecified Expected Results (advisory)",
        "",
        f"{n} case(s) assert a value or threshold the ticket never "
        "fixes. They remain in the suite, but treat them as exploratory: record "
        "what the build actually does instead of trusting the number written here:",
    ]


def unspecified_section(cases: list[TestCase], verdicts: list[Verdict]) -> str:
    """Advisory markdown for cases whose oracle the ticket never fixes.

    These STAY in the suite -- the tester can still run them -- but the expected
    result is an assumption, so the honest artifact is an exploratory charter that
    records the real behaviour rather than an assertion invented here.
    """
    try:
        marked = {v.tc_id: v for v in verdicts or [] if v.verdict == UNSPECIFIED}
        if not marked:
            return ""
        titles = {_tc_id(tc): (getattr(tc, "title", "") or "") for tc in cases or []}
        lines = _unspec_head(len(marked))
        lines += _unspec_lines(marked, titles)
        return "\n".join(lines + _overflow_line(len(marked)))
    except Exception:
        logger.exception("unspecified_section failed - returning empty string")
        return ""


def refusal_section(routing: Routing) -> str:
    """Advisory markdown when a whole verdict batch was refused."""
    try:
        if not routing or not routing.refusal:
            return ""
        return "\n".join(
            [
                "\n\n## Grounding Review Refused (advisory)",
                "",
                f"- {routing.refusal}",
            ]
        )
    except Exception:
        logger.exception("refusal_section failed - returning empty string")
        return ""
