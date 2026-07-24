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


def _neutralize_line(line: str) -> str:
    """Prefix a single line with ' when it starts with a formula trigger."""
    if line and line[0] in _FORMULA_TRIGGERS:
        return "'" + line
    return line


def sanitize_cell(value: str) -> str:
    """Neutralize spreadsheet/CSV formula injection at the start of EVERY line
    within a cell (NB-015).

    Excel, Google Sheets and TestRail's CSV importer treat a cell (and, in
    multi-line cells, effectively each line) that begins with =, +, -, @, TAB or
    CR as a formula. LLM-generated test text is untrusted, and our steps/test-data
    cells are multi-line ("1. do X\\n2. do Y"). Checking only text[0] left a
    formula that appears AFTER an embedded newline (e.g. "1. do X\\n=cmd|...")
    live. We therefore split on newlines and prefix a leading ' on each offending
    line.

    The leading single-quote is the accepted OWASP defense for CSV injection; it
    is invisible in .xlsx (Excel's text marker) and TestRail/Sheets strip it on
    import in the common flows. It can show as a literal ' in a raw CSV opened as
    plain text, which is the documented, accepted trade-off for a safe import.

    Never raises -- non-string input is coerced via str() first.
    """
    text = value if isinstance(value, str) else str(value)
    if not text:
        return text
    # Preserve the original newline characters (\n and \r\n both collapse to \n
    # for our exports, which is fine — we never emit lone \r as data).
    return "\n".join(_neutralize_line(line) for line in text.split("\n"))
