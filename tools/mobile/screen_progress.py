"""One generic progress line for a screen that shows a progress indicator.

Any app, any installer: it reads only the pruned element rows (a progress-bar
class, a percent label, a downloading/installing word) and prints fixed words
plus digits, never device text, so nothing untrusted reaches the packet.
"""

from __future__ import annotations

import re

_BAR = re.compile(r"progress", re.IGNORECASE)
_PERCENT = re.compile(r"^\s*(\d{1,3})\s?%\s*$")
_STATES = (
    ("installing", "installing"),
    ("downloading", "downloading"),
    ("updating", "updating"),
    ("uploading", "uploading"),
)


def _texts(row: dict) -> list[str]:
    return [str(row.get(k) or "") for k in ("text", "label", "desc")]


def _percent(rows: list) -> int | None:
    """The first bare ``NN%`` label as an int, else ``None``."""
    for row in rows:
        for text in _texts(row):
            found = _PERCENT.match(text)
            if found and int(found.group(1)) <= 100:
                return int(found.group(1))
    return None


def _state(rows: list) -> str:
    blob = " ".join(t.lower() for r in rows for t in _texts(r))
    return next((word for key, word in _STATES if key in blob), "")


def _has_bar(rows: list) -> bool:
    return any(
        _BAR.search(str(r.get("cls") or "") + str(r.get("role") or "")) for r in rows
    )


def progress_line(screen: object) -> str:
    """The line for a screen with a progress indicator, else ``""``. Never raises.

    A bare percent label (a discount badge, a battery row) or a bare state word
    (a "Downloads" menu entry) is not an indicator: a bar, or a percent next to
    a state word, is. 100% means finished, so it says nothing."""
    body = screen if isinstance(screen, dict) else {}
    if isinstance(body.get("content"), dict):
        body = body["content"]
    rows = [r for r in body.get("elements") or [] if isinstance(r, dict)]
    pct, state = _percent(rows), _state(rows)
    if pct == 100 or not (_has_bar(rows) or (pct is not None and state)):
        return ""
    shown = " ".join(p for p in (state, "" if pct is None else f"{pct}%") if p)
    return (
        "progress: " + (shown or "an indicator is visible") + " on screen. It may"
        " still be running; use wait_until_text or a wait on the finished state,"
        " do not read run-dir files."
    )
