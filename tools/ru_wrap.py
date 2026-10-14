"""Requirement-unit wrapped-row joining: re-assemble table rows split by hard newlines."""

from __future__ import annotations

import logging

from tools.ru_cells import _section_id_match, _split_row
from tools.ru_consts import _MAX_SOURCE_LINES, _MAX_WRAP_ABSORB

logger = logging.getLogger("tools.requirement_units")


def _absorb_wrap(lines: list[str], index: int, line: str) -> tuple[str, int, bool]:
    """Merge ``line`` with the continuation lines that follow it.

    Returns ``(merged, consumed, closed)``: the joined text, how many following
    lines were absorbed, and whether the joined row ends in a pipe.
    """
    merged = line
    consumed = 0
    closed = False
    while consumed < _MAX_WRAP_ABSORB and index + 1 + consumed < len(lines):
        nxt = lines[index + 1 + consumed].rstrip()
        # A "**DF01**"-style marker starts a new table; never absorb it.
        if _section_id_match(nxt) or not nxt.strip():
            break
        merged = merged + " " + nxt.lstrip()
        consumed += 1
        if merged.rstrip().endswith("|"):
            closed = True
            break
    return merged, consumed, closed


# Only a LONE OPENING CELL is a wrapped row. A line with two or
# more cells is a complete record written without a trailing pipe,
# and treating it as unterminated is what collapsed whole
# descriptions. The real Jira wrapping looks like
#     | < خطأ في معلومات الطلب>
#      | <Incorrect order information > | Checkbox |
# so the fragment has ONE cell and its continuation legitimately
# looks like a complete row -- which means the continuation cannot be
# rejected for looking complete, only the fragment can be rejected
# for looking complete.
def _join_step(lines: list[str], index: int) -> tuple[str, int]:
    """The line to emit for ``lines[index]`` and the index of the next line."""
    line = lines[index].rstrip()
    stripped = line.strip()
    if not (stripped.startswith("|") and not stripped.endswith("|")):
        return line, index + 1
    if len(_split_row(line)) != 1:
        return line, index + 1
    merged, consumed, closed = _absorb_wrap(lines, index, line)
    if closed:
        return merged, index + 1 + consumed
    # Leave it alone -- _split_row tolerates the missing pipe.
    return line, index + 1


def _rejoin(lines: list[str]) -> list[str]:
    """Run :func:`_join_step` over every line and collect what it emits."""
    out: list[str] = []
    index = 0
    while index < len(lines):
        emitted, index = _join_step(lines, index)
        out.append(emitted)
    return out


# Lines of the description with wrapped table rows re-joined.
#
# A Jira-rendered table cell can carry a hard newline, which splits ONE row
# across two lines (`| <cell` / ` | <cell> | Checkbox |`). Parsing those
# separately shifts every later cell by one column and silently drops the DF01
# checkbox labels, so such rows are re-assembled first.
#
# Joining is BOUNDED and REVERSIBLE, because the naive version ("absorb lines
# until one ends in a pipe") had two failure modes, both found in review:
#
# * A table written WITHOUT trailing pipes -- legal GFM, and a common style --
#   made every row look unterminated, so the whole description collapsed into a
#   single line and unit parsing, the enumerations and the source-defect checks
#   all silently returned nothing.
# * An unterminated row absorbed unrelated later rows, splicing them into one
#   fabricated "requirement" and hiding the very duplicate-id defects this
#   module exists to report.
#
# So: at most ``_MAX_WRAP_ABSORB`` continuation lines are absorbed, a line
# that is itself a complete row or a section marker is never absorbed, and if
# the row still does not close the ORIGINAL lines are emitted unchanged. A row
# that legitimately has no trailing pipe is then parsed by ``_split_row``,
# which treats the trailing pipe as optional.
def _join_wrapped_rows(description: str) -> list[str]:
    """Lines of the description with wrapped table rows re-joined (bounded, reversible)."""
    try:
        return _rejoin((description or "").splitlines()[:_MAX_SOURCE_LINES])
    except Exception:
        logger.exception("_join_wrapped_rows failed - using raw lines")
        return (description or "").splitlines()[:_MAX_SOURCE_LINES]
