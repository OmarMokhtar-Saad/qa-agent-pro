"""Replay waits: a change that is only a progress text is not the change waited for.

Measured (``mrun-20261021-162641-0fedce``): a button went ``Download`` ->
``Downloading... 0%`` and back to ``Download``; the wait for the screen to change
passed on the progress text and the case said "Download started". A progress or
busy text is transient: it is recorded as *in progress*, the wait keeps looking,
and a wait that ends on it (or back on the starting screen) says so.
"""

from __future__ import annotations

import re

from tools.mobile.replay.screen import _screen_hash

_KEY = "wait_progress"
_PERCENT = re.compile(r"\b\d{1,3}\s?%")
_PREFIX = (
    r"^(downloading|loading|installing|uploading|updating|syncing|preparing|"
    r"connecting|please wait|in progress)\b"
)
_BUSY = re.compile(_PREFIX + r"|(\.{3}|…)$", re.I)
_BUSY_WORD = re.compile(_PREFIX, re.I)


def _texts(screen: object) -> set[str]:
    """Every non-empty visible text and description on *screen*."""
    rows = screen.get("elements") if isinstance(screen, dict) else None
    found = set()
    for row in rows or []:
        for field in ("text", "desc"):
            value = " ".join(str(row.get(field) or "").split()) if row else ""
            if value:
                found.add(value)
    return found


def is_progress_text(text: str) -> bool:
    """True for a percentage or a busy phrase (``Downloading... 0%``, ``Loading...``)."""
    return bool(_PERCENT.search(text) or _BUSY.search(text))


def is_busy_text(text: str) -> bool:
    """Strict form: a percentage or a busy-prefix text; a bare trailing ``…`` is not enough."""
    return bool(_PERCENT.search(text) or _BUSY_WORD.search(text))


def _grew_into(gone: str, added: set[str]) -> bool:
    """A removed label the progress text grew out of (``Download`` -> ``Downloading...``)."""
    stem = gone.lower()[:4]
    return len(stem) == 4 and any(a.lower().startswith(stem) for a in added)


def added_progress(before: object, now: object) -> list[str]:
    """The progress texts *now* gained over *before* when they are the ONLY change, else ``[]``."""
    old, new = _texts(before), _texts(now)
    added, gone = new - old, old - new
    if not added or not all(is_progress_text(a) for a in added):
        return []
    if all(is_progress_text(g) or _grew_into(g, added) for g in gone):
        return sorted(added)
    return []


def note_progress(ctx: object, seen: list[str]) -> None:
    """Remember the progress texts a change wait saw (read by :func:`stall_detail`)."""
    env = getattr(ctx, "env", None)
    if env is not None:
        env.setdefault(_KEY, []).extend(seen)


def take_progress(ctx: object) -> list[str]:
    """The progress texts the last change wait saw; cleared."""
    return list((getattr(ctx, "env", None) or {}).pop(_KEY, None) or [])


def stall_detail(seen: list[str], ends: tuple, secs: str) -> str:
    """The sentence for a change wait that saw only progress; ``""`` when none was seen.

    *ends* is ``(screen the wait started on, screen it ended on)``.
    """
    if not seen:
        return ""
    last = repr(seen[-1])
    if _screen_hash(ends[0]) == _screen_hash(ends[1]):
        return (
            "the action did not stick: the screen showed only "
            + last
            + " (progress), then went back to how it was before, within "
            + secs
        )
    if len(set(seen)) == 1:
        return "progress stuck at " + last + " for " + secs + ": not done"
    return "still in progress (" + last + ") after " + secs + ": not done yet"
