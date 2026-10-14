"""Header-row and numbered-list parsing of the case importers."""

from __future__ import annotations

from tools.mobile.imp_consts import _STEP_SPLIT_RE, COLUMN_ALIASES, MAX_STEPS
from tools.mobile.imp_text import _clean, _key


def _map_headers(header_row: list[str]) -> dict[str, int]:
    """Column name -> index. Case-insensitive, first match wins."""
    mapping: dict[str, int] = {}
    for index, raw in enumerate(header_row):
        key = _key(raw)
        if not key:
            continue
        for field, aliases in COLUMN_ALIASES.items():
            if field in mapping:
                continue
            if key in aliases:
                mapping[field] = index
                break
    return mapping


def _split_numbered(text: str) -> list[str]:
    """``"1. a\\n2. b"`` -> ``["a", "b"]``; unnumbered lines stay one per line."""
    body = _clean(text)
    if not body:
        return []
    parts = _STEP_SPLIT_RE.split(body)
    if len(parts) > 1:
        out = []
        # split() yields [pre, num, text, num, text, ...]
        for index in range(1, len(parts) - 1, 2):
            piece = _clean(parts[index + 1])
            if piece:
                out.append(piece)
        if out:
            return out[:MAX_STEPS]
    plain = (_clean(piece) for piece in body.split("\n"))
    return [line for line in plain if line][:MAX_STEPS]
