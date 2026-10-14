"""Crash detector match rules: which marker a line proves and who it names."""

from __future__ import annotations

from tools.mobile_evidence.crash_const import (
    _LINE_RE,
    _TAGS_AM,
    _TAGS_CRASH,
    _TAGS_NATIVE,
    KIND_ANR,
    KIND_APP_CRASH,
    KIND_NATIVE_ABORT,
    KIND_PROCESS_DEATH,
    MAX_ATTRIBUTION_LINES,
)


def _kind_of(tag: str, message: str) -> str:
    """The kind this line's TAG and text prove, or "". Clause 2 lives here."""
    low = tag.strip().lower()
    body = message
    if low in _TAGS_CRASH and "FATAL EXCEPTION" in body:
        return KIND_APP_CRASH
    if low in _TAGS_NATIVE and ("Fatal signal" in body or "tombstone" in body):
        return KIND_NATIVE_ABORT
    if low in _TAGS_AM and body.lstrip().startswith("ANR in "):
        return KIND_ANR
    if low in _TAGS_AM and (
        ("has died" in body and body.lstrip().startswith("Process "))
        or body.lstrip().startswith("Force finishing activity ")
    ):
        return KIND_PROCESS_DEATH
    return ""


def _attributes(kind: str, message: str, matcher) -> bool:
    """Whether THIS line attributes *kind* to the package. Clause 3, same line."""
    if kind == KIND_APP_CRASH:
        return bool(message.lstrip().startswith("Process:")) and bool(
            matcher.search(message)
        )
    if kind == KIND_NATIVE_ABORT:
        return ">>>" in message and bool(matcher.search(message))
    return bool(matcher.search(message))


def _parse_lines(lines: list[str]) -> list[tuple[str, str, str]]:
    """``(tag, message, raw line)`` per line; an unparseable line has an empty tag."""
    parsed = []
    for line in lines:
        hit = _LINE_RE.match(line)
        parsed.append((hit.group(2), hit.group(3), line) if hit else ("", "", line))
    return parsed


def _find_marker(parsed: list, matcher) -> tuple[tuple[int, str] | None, str]:
    """``((index, kind) of the first attributed marker or None, last kind seen)``."""
    seen_marker = ""
    for index, (tag, message, _line) in enumerate(parsed):
        if not tag:
            continue
        kind = _kind_of(tag, message)
        if not kind:
            continue
        seen_marker = kind
        window = parsed[index : index + 1 + MAX_ATTRIBUTION_LINES]
        if any(_attributes(kind, entry[1], matcher) for entry in window if entry[0]):
            return (index, kind), seen_marker
    return None, seen_marker
