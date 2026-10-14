"""Crash detector scan: judge one logcat slice for the package under test."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise
from tools.mobile_evidence.crash_const import (
    KIND_LABELS,
    MAX_MARKER_CHARS,
    MAX_SCAN_BYTES,
)
from tools.mobile_evidence.crash_match import _find_marker, _parse_lines
from tools.mobile_evidence.crash_text import (
    SliceFacts,
    _empty,
    _excerpt,
    _missed,
    _neutralize,
    _package_re,
)

logger = logging.getLogger("tools.mobile_evidence.crash_detector")


def _text_of(text: object) -> str | None:
    """The slice as text, or None when its ``__str__`` raises."""
    # a __str__ that raises
    with never_raise():
        return text if isinstance(text, str) else str(text or "")
    return None


def _prepare(text: object, pkg: str) -> str | dict:
    """The slice text to judge, or the early not-detected record. Order is the contract."""
    if not pkg:
        return _empty("no package under test was given, so nothing can be attributed")
    body = _text_of(text)
    if body is None:
        return _empty("the slice could not be read as text")
    if not body.strip():
        return _empty("the slice is empty, so it proves nothing either way")
    return body


def _tail(body: str) -> tuple[str, bool]:
    """The body clipped to MAX_SCAN_BYTES, and whether it was clipped."""
    raw = body.encode("utf-8", errors="replace")
    truncated = len(raw) > MAX_SCAN_BYTES
    if truncated:
        # THE TAIL, not the head. A crash ends the process.
        body = raw[-MAX_SCAN_BYTES:].decode("utf-8", errors="replace")
    return body, truncated


def _hit(parsed: list, found: tuple[int, str], facts: SliceFacts) -> dict:
    """The detected record for the marker found at ``found`` = (index, kind)."""
    index, kind = found
    return {
        "detected": True,
        "kind": kind,
        "label": KIND_LABELS.get(kind, "the app under test died"),
        "marker": _neutralize(parsed[index][2])[:MAX_MARKER_CHARS],
        "excerpt": _excerpt(parsed, index),
        "package": facts.pkg[:120],
        "lines_scanned": facts.lines,
        "truncated": facts.truncated,
        "reason": "",
    }


def _judge(body: str, pkg: str, truncated: bool) -> dict:
    """Parse the lines and decide: detected record or a reasoned miss."""
    lines = body.splitlines()
    matcher = _package_re(pkg)
    parsed = _parse_lines(lines)
    found, seen = _find_marker(parsed, matcher)
    facts = SliceFacts(pkg, len(lines), truncated)
    return _hit(parsed, found, facts) if found is not None else _missed(seen, facts)


def _scan_raw(text: object, pkg: str) -> dict:
    body = _prepare(text, pkg)
    if isinstance(body, dict):
        return body
    body, truncated = _tail(body)
    return _judge(body, pkg, truncated)


def scan(text: object, package: object) -> dict:
    """Judge one slice. ``{"detected", "kind", "label", "marker", "excerpt",
    "package", "lines_scanned", "truncated", "reason"}``. Never raises.

    ``detected`` is True only with a kind AND a marker line AND the package under
    test attributed to that marker.
    """
    # never-raise: a detector that raises is a detector that is off
    with never_raise(
        logger,
        "mobile_evidence.crash_detector.scan failed",
        level=logging.ERROR,
        exc_info=True,
    ):
        return _scan_raw(text, str(package or "").strip())
    return _empty("the slice could not be scanned")
