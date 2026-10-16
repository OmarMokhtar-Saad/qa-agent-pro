"""Per-process cache of the doctor report (fix/1210 G5).

A second qa-doctor call in the same run returns the first report with a note
instead of re-probing the machine. fix=True always runs fresh, a different
workspace never sees another's report, and a report carrying the one-shot
reload notice or a red (❌) row is never kept (a failure is always re-probed).
"""

from __future__ import annotations

import time

TTL_S = 300.0
_NOTE = "(cached from earlier in this run, nothing was red then; ask again after 5 minutes for a fresh read)"
_FAILED = "⚠️ Setup check failed"
_ONE_SHOT = "**Reload"
_RED = "❌"
_entry: tuple[float, str, str] | None = None


def roots_key(workspace_roots) -> str:
    """The cache key for the tester's open workspace folders."""
    return "\n".join(sorted(str(r) for r in workspace_roots or ()))


def lookup(fix: bool, key: str = "", now: float | None = None) -> str | None:
    """The cached report with a note, or None when a fresh run is needed."""
    if fix or _entry is None or _entry[1] != key:
        return None
    at = time.monotonic() if now is None else now
    if at - _entry[0] > TTL_S:
        return None
    return _NOTE + "\n" + _entry[2]


def store(fix: bool, report: str, key: str = "") -> None:
    """Remember a fresh report with no red row (never a fix run, a failure or a reload notice)."""
    global _entry
    if fix or report.startswith(_FAILED) or _ONE_SHOT in report or _RED in report:
        return
    _entry = (time.monotonic(), key, report)


def reset() -> None:
    global _entry
    _entry = None
