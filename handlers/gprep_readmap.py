"""Reading map for a long host-mode PREPARE payload (K-14).

Some hosts cap how many lines one read may cover (about 200). The payload
stays ONE parseable JSON block; this adds a short map of where each top-level
section and each category starts, so a host can read it in parts."""

from __future__ import annotations

import re

_READ_WINDOW = 200
_MAP_ENTRIES = 40
_TOP_KEY = re.compile(r'^  "([^"]+)":')
_CAT_START = re.compile(r"^    \{")


def _starts(lines: list[str]) -> list[tuple[str, int]]:
    """(label, 1-based line) for each top-level key and each categories[i]."""
    found: list[tuple[str, int]] = []
    in_cats, n_cat = False, 0
    for no, line in enumerate(lines, 1):
        top = _TOP_KEY.match(line)
        if top:
            in_cats = top.group(1) == "categories"
            found.append((top.group(1), no))
        elif in_cats and _CAT_START.match(line):
            found.append((f"categories[{n_cat}]", no))
            n_cat += 1
    return found


def reading_map(body: str, fence_line: int) -> list[str]:
    """Lines to print above a fenced JSON body; [] when it fits one read window.

    ``fence_line`` is the 1-based reply line of the opening ``json`` fence, so
    JSON line N sits on reply line ``fence_line + N``."""
    lines = body.split("\n")
    if len(lines) <= _READ_WINDOW:
        return []
    shown = _starts(lines)[:_MAP_ENTRIES]
    where = ", ".join(f"`{k}` L{no}" for k, no in shown)
    return [
        "",
        f"**Reading map:** the JSON below is {len(lines)} lines; if your reader "
        f"caps a read at {_READ_WINDOW} lines, read it in parts. `L` numbers "
        f"count from the `{{` that opens the JSON (JSON line 1). The opening "
        f"fence is line {fence_line} of this reply, so JSON line N is reply "
        f"line {fence_line} + N (add the offset if text sits above this "
        f"reply): {where}.",
    ]
