"""Say so when a wait timed out on the very screen a tap started from."""

from __future__ import annotations

_TAPS = ("tap", "tap_text")
_TAP_NOTE = " The screen was still unchanged when the following wait timed out."
_WAIT_NOTE = (
    " The tap before it left the screen as it was, so it may not have registered."
)


def _is_ok_tap(entry: dict) -> bool:
    action = entry.get("action")
    op = action.get("op") if isinstance(action, dict) else None
    return op in _TAPS and entry.get("outcome") == "ok"


def note_dead_tap(trace: list) -> None:
    """Annotate an ok tap and the wait_timeout that followed it, when the wait's
    final polled screen equals the screen the tap began on. Advisory only."""
    if len(trace) < 2:
        return
    tap, wait = trace[-2], trace[-1]
    if not _is_ok_tap(tap) or wait.get("outcome") != "wait_timeout":
        return
    seen = wait.get("after_screen_hash")
    if not seen or seen != tap.get("before_screen_hash"):
        return
    tap["detail"] = str(tap.get("detail") or "") + _TAP_NOTE
    wait["detail"] = str(wait.get("detail") or "") + _WAIT_NOTE
