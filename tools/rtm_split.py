"""Split raw acceptance-criteria text into cleaned candidate lines."""

from __future__ import annotations

import logging
import re

from tools import rtm_labels as _lb
from tools.rtm_labels import (
    _AC_LABEL_RE,
    _AC_SPLIT_RE,
    _ANY_LABEL_RE,
    _BARE_SECTION_RE,
    _INLINE_LABEL_SPLIT_RE,
    _NON_AC_PREFIX_RE,
)
from tools.rtm_looks import looks_like_requirement_text

logger = logging.getLogger("tools.rtm")


def _expand_inline_labels(lines: list[str]) -> list[str]:
    """Split each line on its inline labels when at least two are present."""
    # 2026-08-31 (F3): a Business-Rules table flattens to ONE line carrying
    # every `BRnn:` label, so neither split above separates anything. Split
    # on the label itself -- only where at least two are present.
    expanded: list[str] = []
    for _ln in lines:
        _text = _ln if isinstance(_ln, str) else ""
        if len(_ANY_LABEL_RE.findall(_text)) >= _lb._MIN_INLINE_LABELS:
            expanded += [p for p in _INLINE_LABEL_SPLIT_RE.split(_text) if p.strip()]
        else:
            expanded.append(_text)
    return expanded


# Split on bullet or numbered list markers at the start of a line,
# or on double-newlines (paragraph breaks).
#
# If that produced only one non-empty chunk -- or if any single chunk
# STILL carries more than one `ACn:` label, which is exactly what a
# trailing paragraph used to hide -- fall back to single-newline split.
def _split_ac_lines(raw: str) -> list[str]:
    """Split raw AC text into candidate lines (list markers, paragraphs, labels)."""
    lines = re.split(_AC_SPLIT_RE, raw)
    non_empty = [ln.strip() for ln in lines if ln.strip()]
    if len(non_empty) <= 1 or any(
        len(_AC_LABEL_RE.findall(ln)) > 1 for ln in non_empty
    ):
        lines = raw.splitlines()
    return _expand_inline_labels(lines)


def _strip_list_marker(line: str) -> str:
    """Strip surrounding whitespace and any residual leading list marker."""
    line = line.strip()
    # Strip any residual leading list marker that survived the split.
    # A bare digit is CONTENT (e.g. "3 failed logins", "200ms"); only
    # strip a leading number when it is a real list marker — i.e. it is
    # immediately followed by a delimiter (./)/]) AND whitespace.
    return re.sub(
        r"^\s*(?:[-*•]|\d+[.)\]]|AC\s*-?\s*\d{1,3}\s*[:.)\]-])\s+",
        "",
        line,
    ).strip()


def _is_dropped(line: str) -> bool:
    """True when a cleaned candidate is a section label or not a requirement."""
    # A trailing "Notes:"/"Links:" paragraph is prose ABOUT the ticket,
    # not a criterion -- see _NON_AC_PREFIX_RE.
    if _BARE_SECTION_RE.match(line):
        logger.debug("Dropping bare section heading: %.60r", line)
        return True
    if _NON_AC_PREFIX_RE.match(line):
        logger.debug("Dropping non-criterion section label: %.60r", line)
        return True
    # A configured AC field can return something that is not a
    # requirement at all (a date, an id, a number). Letting it
    # through creates a bogus AC that every downstream anchoring
    # check then treats as ground truth.
    if not looks_like_requirement_text(line):
        logger.debug("Dropping non-requirement AC candidate: %.60r", line)
        return True
    return False


def _accept_ac_candidate(line: str) -> str:
    """Return the cleaned criterion text for one candidate line, or "" to drop it."""
    line = _strip_list_marker(line)
    if len(line) < 5:
        return ""
    return "" if _is_dropped(line) else line
