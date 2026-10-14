"""The checks as tester-facing markdown."""

from __future__ import annotations

import logging

logger = logging.getLogger("tools.mobile.preflight")


def _advisory_failure(record: dict) -> bool:
    return (not record.get("ok")) and record.get("blocking", True) is False


# Blocking failures first, then advisory ones, then the passes.
def _sort_key(record: dict) -> tuple[bool, bool]:
    return (bool(record.get("ok")), _advisory_failure(record))


def _mark(record: dict, advisory: bool) -> str:
    return "✅" if record.get("ok") else ("⚠️" if advisory else "❌")


def _record_lines(record: dict) -> list[str]:
    advisory = _advisory_failure(record)
    label = " (advisory, does not block a run)" if advisory else ""
    lines = [
        _mark(record, advisory)
        + " **"
        + str(record.get("name"))
        + "**"
        + label
        + " — "
        + str(record.get("detail"))
    ]
    if not record.get("ok") and record.get("fix"):
        lines.append("   ↳ " + str(record["fix"]))
    return lines


def render(content: dict) -> str:
    """The checks as tester-facing markdown, failures first with their fixes."""
    body = dict(content or {})
    ordered = sorted(body.get("checks") or [], key=_sort_key)
    lines: list[str] = []
    for record in ordered:
        lines.extend(_record_lines(record))
    return "\n".join(lines)
