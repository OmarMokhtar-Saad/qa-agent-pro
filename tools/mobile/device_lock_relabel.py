"""The all-or-nothing relabel hand-off, split out of :mod:`tools.mobile.device_lock`.

Imports :mod:`tools.mobile.locks` only, never ``device_lock``.
"""

from __future__ import annotations

from tools.mobile import locks


def _roll_back(done: list[str], old: str, new: str) -> None:
    """Put the already-moved locks back under *old*; release one that cannot move."""
    for back in done:
        undone = locks.relabel(back, from_owner=new, to_owner=old)
        if not ((undone or {}).get("content") or {}).get("relabelled"):
            locks.release(back, locks.Release(owner=new, as_holder=True))


def _move_all(names: list[str], old: str, new: str) -> dict | None:
    """Relabel every name to *new*; ``None`` on success, else the refusal (rolled back)."""
    done: list[str] = []
    for name in names:
        moved = locks.relabel(name, from_owner=old, to_owner=new)
        if not ((moved or {}).get("content") or {}).get("relabelled"):
            # ALL OR NOTHING. A half hand-off leaves some locks under the run
            # and some under the pre-run label, and the caller's `finally`
            # releases only the latter -- the rest would be held by a run that
            # believes it owns nothing. Put the moved ones back; if one cannot
            # be put back, release it rather than leave a run-owned hold.
            _roll_back(done, old, new)
            return moved
        done.append(name)
    return None
