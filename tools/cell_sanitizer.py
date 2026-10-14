"""Formula-injection defense for spreadsheet/CSV exports.

Excel, Google Sheets, and TestRail's CSV importer all treat a cell that starts
with =, +, -, @, TAB, or CR as a formula to evaluate. LLM-generated test case
text (titles, steps, test data, risk rationale) is untrusted content by the
time it reaches an exporter, so every exporter that writes it into a
spreadsheet cell must neutralize a leading formula-trigger character before
writing (QW-2 / I-003 / B-003).
"""

from __future__ import annotations

_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


# Characters an importer may skip before deciding a cell is a formula, so
# NBSP + "=cmd" is still live: NBSP, zero-width, other Unicode spaces, C0 controls.
_LEADING_SKIP = (
    "\u00a0\u200b\u200c\u200d\u2060\ufeff\u202f\u205f\u3000 "
    + "".join(chr(c) for c in range(0x2000, 0x200B))
    + "".join(
        chr(c) for c in (*range(0x00, 0x09), 0x0B, 0x0C, *range(0x0E, 0x20), 0x7F)
    )
)


def _prefix(segment: str) -> str:
    if not segment:
        return segment
    head = segment.lstrip(_LEADING_SKIP)
    if segment[0] in _FORMULA_TRIGGERS or (head and head[0] in _FORMULA_TRIGGERS):
        return "'" + segment
    return segment


def _neutralize_line(line: str) -> str:
    """Prefix a line (and each lone-CR sub-line) that starts with a formula trigger.

    A lone \\r starts a new row in Excel, so text after one is a line start too.
    """
    line = _prefix(line)
    if "\r" in line:
        first, *rest = line.split("\r")
        line = "\r".join([first, *(_prefix(p) for p in rest)])
    return line


# Excel, Google Sheets and TestRail's CSV importer treat a cell (and, in
# multi-line cells, effectively each line) that begins with =, +, -, @, TAB or
# CR as a formula. LLM-generated test text is untrusted, and our steps/test-data
# cells are multi-line ("1. do X\\n2. do Y"). Checking only text[0] left a
# formula that appears AFTER an embedded newline (e.g. "1. do X\\n=cmd|...")
# live. We therefore split on newlines and prefix a leading ' on each offending
# line.
#
# The leading single-quote is the accepted OWASP defense for CSV injection; it
# is invisible in .xlsx (Excel's text marker) and TestRail/Sheets strip it on
# import in the common flows. It can show as a literal ' in a raw CSV opened as
# plain text, which is the documented, accepted trade-off for a safe import.
#
# Never raises -- non-string input is coerced via str() first.
def sanitize_cell(value: str) -> str:
    """Neutralize spreadsheet/CSV formula injection at the start of EVERY line
    within a cell (NB-015)."""
    text = value if isinstance(value, str) else str(value)
    if not text:
        return text
    # Preserve the original newline characters (\n and \r\n both collapse to \n
    # for our exports, which is fine; a lone \r is handled in _neutralize_line).
    return "\n".join(_neutralize_line(line) for line in text.split("\n"))
