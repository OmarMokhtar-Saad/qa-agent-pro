"""Requirement-unit table cells: cleaning, row splitting, section-id and label tests."""

from __future__ import annotations

import logging
import re

from tools import ru_patterns as rp
from tools.ru_consts import _UC_LABEL_KINDS

logger = logging.getLogger("tools.requirement_units")


def _clean_cell(raw: str) -> str:
    """Strip markdown emphasis, known HTML/ADF tags and collapse whitespace."""
    try:
        text = rp._CUSTOM_TAG_RE.sub(" ", raw or "")
        text = rp._HTML_TAG_RE.sub(" ", text)
        text = rp._EMPHASIS_RE.sub("", text)
        text = rp._STRAY_BACKSLASH_RE.sub("", text)
        return rp.WS_RE.sub(" ", text).strip()
    except Exception:
        logger.exception("_clean_cell failed - returning empty string")
        return ""


def _section_id_match(line: str) -> re.Match[str] | None:
    """The single entry point for the marker test.

    Retained after the length bound it used to apply was deleted as unreachable
    (see the note above ``_SECTION_ID_RE``): one call site for the pattern is
    still worth having, because the 2026-09-02 audit found this pattern
    reachable from a Jira description through TWO call sites (:269 and :491) and
    a future bound applied at one of them would be forgotten at the other.
    Never raises.
    """
    return rp.SECTION_ID_RE.match(line)


# Cells of a markdown pipe-table row, or [] when the line is not one.
#
# The trailing pipe is OPTIONAL: `| a | b | c` is as legal in GFM as
# `| a | b | c |`, and requiring it meant a table in that style produced no
# rows at all -- which silently disabled every check built on this module.
def _split_row(line: str) -> list[str]:
    """Cells of a markdown pipe-table row, or [] when the line is not one."""
    try:
        if rp._TABLE_SEP_RE.match(line):
            return []
        stripped = (line or "").strip()
        if not stripped.startswith("|"):
            return []
        body = stripped[1:]
        if body.endswith("|"):
            body = body[:-1]
        if not body.strip():
            return []
        return [_clean_cell(cell) for cell in body.split("|")]
    except (AttributeError, TypeError):
        return []


def _label_kind(label: str) -> str | None:
    """The unit kind a use-case table row label maps to, or None."""
    key = _clean_cell(label).lower().rstrip(":").strip()
    return _UC_LABEL_KINDS.get(key)
