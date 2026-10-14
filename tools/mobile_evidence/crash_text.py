"""Crash detector text helpers: neutralising device text, excerpts, miss records."""

from __future__ import annotations

import re
from dataclasses import dataclass

from config.settings_guard import never_raise
from tools.mobile_evidence.crash_const import (
    _CONTROL_RE,
    _WS_RE,
    MAX_EXCERPT_CHARS,
    MAX_EXCERPT_LINES,
)
from tools.untrusted import GUARD_MARKERS, NEUTRALIZED


@dataclass(frozen=True)
class SliceFacts:
    """What the scan knows about the slice: package, line count, tail-clipped."""

    pkg: str
    lines: int
    truncated: bool


def _neutralize(value: object) -> str:
    """One device-sourced string, safe to STORE and to SHOW. Never raises."""
    # a __str__ that raises
    with never_raise() as g:
        text = value if isinstance(value, str) else str(value or "")
    if g.failed:
        return ""
    text = _CONTROL_RE.sub("", text).replace("\r", " ").replace("\n", " ")
    for marker in GUARD_MARKERS:
        if marker and marker.lower() in text.lower():
            text = re.sub(re.escape(marker), NEUTRALIZED, text, flags=re.IGNORECASE)
    return _WS_RE.sub(" ", text).strip()


def _empty(reason: str, *, lines: int = 0, truncated: bool = False) -> dict:
    """The not-detected record. Always carries a reason: silence is not evidence."""
    return {
        "detected": False,
        "kind": "",
        "label": "",
        "marker": "",
        "excerpt": "",
        "package": "",
        "lines_scanned": int(lines),
        "truncated": bool(truncated),
        "reason": str(reason)[:200],
    }


def _package_re(package: str):
    """A WHOLE-package matcher. ``com.example.app`` must not match
    ``com.example.app.debug`` -- clause 4, and the reason this is a regex rather
    than an ``in``."""
    return re.compile(r"(?<![\w.])" + re.escape(package) + r"(?![\w.])")


def _excerpt(parsed: list, index: int) -> str:
    return "\n".join(
        _neutralize(entry[2]) for entry in parsed[index : index + MAX_EXCERPT_LINES]
    )[:MAX_EXCERPT_CHARS]


def _missed(seen_marker: str, facts: SliceFacts) -> dict:
    """The not-detected record, saying whether another process's marker was seen."""
    if seen_marker:
        reason = (
            "the "
            + seen_marker
            + " marker in this slice names another process, not "
            + facts.pkg[:80]
            + ", so it is not this case's failure"
        )
    else:
        reason = "no fatal-event marker from the platform is in this slice"
    return _empty(reason, lines=facts.lines, truncated=facts.truncated)
