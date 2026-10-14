"""Only a proven failure is learned: not an ``unknown`` one, not one the run contradicted."""

from __future__ import annotations

UNPROVEN_OUTCOME = "unknown"
_CONTRADICTABLE = ("text_not_landed", "element_missing", "dead_tap")
_OK = ("ok", "assert_pass", "visual_check", "done")


def _moved_after(rec: dict) -> bool:
    after = str(rec.get("after_screen_key") or "")
    return bool(after) and after != rec.get("screen_key")


def _advanced(steps: list, i: int) -> bool:
    """The next step worked on another screen: the run moved on past this one."""
    if i + 1 >= len(steps):
        return False
    nxt = steps[i + 1]
    worked = str(nxt.get("outcome") or "") in _OK
    return worked and nxt.get("screen_key") != steps[i].get("screen_key")


def proven(steps: list, i: int, sig: str) -> bool:
    """Whether failure *sig* on ``steps[i]`` stands (nothing unknown or contradicted)."""
    rec = steps[i]
    if str(rec.get("outcome") or "") == UNPROVEN_OUTCOME:
        return False
    if sig not in _CONTRADICTABLE:
        return True
    if sig == "text_not_landed" and _moved_after(rec):
        return False
    return not _advanced(steps, i)
