"""Key/value tables and labelled pane sections on an exchange row."""

from __future__ import annotations

from tools.mobile_evidence.exch_seqargs import SecMeta
from tools.mobile_shared.scrub import (
    SENSITIVE_KEY_RE,
    e,
    mask_value,
    redact_header,
    scrub_text,
)

_MARKED_KEYS = {"authorization", "apikey", "accept-language"}


def _kv_value(key: object, value: object) -> object:
    """One cell's value: header-redacted, then masked (sensitive key) or scrubbed."""
    value = redact_header(key, value)
    if SENSITIVE_KEY_RE.search(str(key)):
        return mask_value(value) if str(value).strip() else value
    return scrub_text(str(value))


def _kv_row(key: object, value: object, mark: bool) -> str:
    """One ``<tr>``; ``mark`` highlights the header names a reader checks first."""
    highlight = ' class="hl"' if mark and str(key).lower() in _MARKED_KEYS else ""
    value = _kv_value(key, value)
    blank = not str(value).strip()
    shown = "\u2014" if blank else str(value)
    return (
        "<tr"
        + highlight
        + '><td class="hk">'
        + e(key)
        + '</td><td class="hv'
        + (" blank" if blank else "")
        + '" dir="auto">'
        + e(shown)
        + "</td></tr>"
    )


def kvtable(pairs: dict | None, mark: bool = False) -> str | None:
    """Key/value pairs as a table, or None when empty. The header mask and both nets apply
    per row: a tool argument row is where an id was once rendered, not a body."""
    if not pairs:
        return None
    rows = []
    for key, value in sorted(pairs.items(), key=lambda item: str(item[0])):
        rows.append(_kv_row(key, value, mark))
    return (
        '<div class="tabwrap"><table class="htab"><tbody>'
        + "".join(rows)
        + "</tbody></table></div>"
    )


def _empty_section(title: str, note: str) -> str:
    """A section with nothing behind it: its label, a ghost chevron, the note or 'empty'."""
    return (
        '<div class="sec empty"><h5><span class="chev ghost" aria-hidden="true"></span>'
        + e(title)
        + '<span class="mt">'
        + (e(note) if note else "empty")
        + "</span></h5></div>"
    )


# An empty section keeps its label (so a pane still reads Query / Headers / Body in the same
# order every time) but carries NO chevron: a disclosure control that toggles nothing lies
# about there being something behind it.
def section(title: str, content: str | None, meta: SecMeta | None = None) -> str:
    """One labelled pane section -- collapsible when it has content, inert when it does not."""
    if meta is None:
        meta = SecMeta()
    if content is None:
        return _empty_section(title, meta.note)
    n = '<span class="cnt">' + str(int(meta.count)) + "</span>" if meta.count else ""
    mt = '<span class="mt">' + e(meta.note) + "</span>" if meta.note else ""
    return (
        '<details class="sec"><summary><span class="chev" aria-hidden="true"></span>'
        + e(title)
        + n
        + mt
        + '</summary><div class="secbody">'
        + content
        + "</div></details>"
    )
