"""Replay: a wait that timed out while progress is visible is in progress, not failed (H03).

A ``wait_until_text`` for ``Download complete`` that ran out of ``max_s`` with
``Downloading... 40%`` still on screen did not fail: the work is moving. The
reply says "still in progress: wait again" and the run closes as abandoned.
"""

from __future__ import annotations

from tools.mobile.replay.waits_progress import _texts, is_busy_text


def progress_on(screen: object) -> list[str]:
    """The progress or busy texts visible on *screen*, sorted."""
    return sorted(t for t in _texts(screen) if is_busy_text(t))


def note_in_progress(entry: dict, screen: object) -> None:
    """Re-word a timed-out wait *entry* when *screen* still shows progress."""
    seen = progress_on(screen)
    if not seen or entry.get("outcome") != "wait_timeout":
        return
    entry["in_progress"] = True
    entry["detail"] = (
        "still in progress (%r) when the wait ran out: not failed, wait again "
        "(longer, or wait_until_gone on it). %s" % (seen[-1], entry.get("detail") or "")
    ).strip()[:300]
