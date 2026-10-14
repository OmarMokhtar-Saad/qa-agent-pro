"""Bodies on an exchange row: sniffed, scrubbed, highlighted and capped."""

from __future__ import annotations

import json
import re

from tools.mobile_shared.scrub import e, scrub_json, scrub_text

BODY_LIMIT = 6000

_JSON_TOK = re.compile(
    r'("(?:\\.|[^"\\])*")\s*:'  # key
    r'|("(?:\\.|[^"\\])*")'  # string
    r"|(-?\d+\.?\d*(?:[eE][+-]?\d+)?)"  # number
    r"|\b(true|false|null)\b"  # literal
)


def _hl_json(text: str) -> str:
    out: list[str] = []
    last = 0
    for match in _JSON_TOK.finditer(text):
        out.append(e(text[last : match.start()]))
        if match.group(1):
            out.append(
                '<span class="jk">'
                + e(match.group(1))
                + '</span><span class="jp">:</span>'
            )
        elif match.group(2):
            out.append('<span class="js">' + e(match.group(2)) + "</span>")
        elif match.group(3):
            out.append('<span class="jn">' + e(match.group(3)) + "</span>")
        else:
            out.append('<span class="jb">' + e(match.group(4)) + "</span>")
        last = match.end()
    out.append(e(text[last:]))
    return "".join(out)


def _sniff_json(stripped: str) -> tuple[str, str]:
    """A body that opens like JSON: pretty-printed if it parses, else scrubbed text."""
    try:
        # Scrubbed as a STRUCTURE, before it is ever a string: walking the parsed object
        # is what lets a key be judged by its name while it is still attached to its value.
        return "json", json.dumps(
            scrub_json(json.loads(stripped)), ensure_ascii=False, indent=2
        )
    except (ValueError, TypeError):
        return "text", scrub_text(stripped)


def _sniff(text: str) -> tuple[str, str]:
    """Classify a body so it can be labelled and highlighted. Never guesses silently."""
    stripped = text.strip()
    if not stripped:
        return "empty", ""
    if stripped[0] in "{[":
        return _sniff_json(stripped)
    stripped = scrub_text(stripped)
    if stripped.startswith("<?xml") or (
        stripped.startswith("<") and stripped.endswith(">")
    ):
        return "xml", stripped
    if re.match(r"^\s*(query|mutation|subscription|fragment)\b", stripped):
        return "graphql", stripped
    return "text", stripped


def _clip_note(pretty: str, limit: int) -> str:
    """The 'truncated' strip under a body that ran past the limit, else nothing."""
    if len(pretty) <= limit:
        return ""
    return (
        '<div class="clip">truncated \u2014 '
        + format(len(pretty) - limit, ",")
        + " more characters</div>"
    )


def _code_pre(kind: str, clipped: str) -> str:
    """The ``<pre>``: JSON pinned left-to-right and highlighted, prose ``dir="auto"``."""
    if kind == "json":
        return '<pre dir="ltr">' + _hl_json(clipped) + "</pre>"
    return '<pre dir="auto">' + e(clipped) + "</pre>"


# JSON is pinned left-to-right (its syntax is), prose takes ``dir="auto"`` so a
# right-to-left body lays out correctly.
def codebox(text: object, limit: int = BODY_LIMIT) -> str | None:
    """A body as a labelled code block, or None when there is nothing to show."""
    kind, pretty = _sniff("" if text is None else str(text))
    if kind == "empty":
        return None
    return (
        '<div class="code"><div class="codetag">'
        + kind.upper()
        + "</div>"
        + _code_pre(kind, pretty[:limit])
        + _clip_note(pretty, limit)
        + "</div>"
    )
