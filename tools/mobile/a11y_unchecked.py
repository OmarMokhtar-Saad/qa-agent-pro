"""A repeat uiautomator could not check is not re-checked at full price every step.

On an animated screen (a progress bar, a spinner) uiautomator waits for the UI to
go idle and one dump takes 10 s or more, past the confirmation cap. The a11y tree
then repeated across an action, the confirmation failed, the provider refused, and
``adb.uiautomator_dump`` read the screen AGAIN: two dumps, then the same on the
next step. After the first such refusal for a digest, the next
:data:`MAX_REUSES` repeats of that same digest are served without a confirmation
(note ``a11y_repeat_reused``); then uiautomator checks once more. Another digest,
a stale verdict or a fresh enable forgets it.
"""

from __future__ import annotations

from tools.mobile import step_timing

#: Repeats of one unchecked digest served without a confirming dump.
MAX_REUSES = 3
REUSED_NOTE = "a11y_repeat_reused"
UNCHECKED_NOTE = "a11y_repeat_unchecked"
UNCHECKED_REFUSAL = "the a11y tree repeated across an action and could not be checked"

#: serial -> [digest uiautomator could not check, reuses left]. One row per device.
_ROWS: dict[str, list] = {}


def mark(serial: str, digest: str) -> None:
    """Remember that *digest* could not be confirmed on *serial*."""
    _ROWS[str(serial)] = [digest, MAX_REUSES]


def refusal(serial: str, digest: str) -> dict:
    """The refusal for a repeat nothing could check; remembers *digest*."""
    step_timing.note(UNCHECKED_NOTE)
    mark(serial, digest)
    return {"error": UNCHECKED_REFUSAL, "content": None}


def reuse(serial: str, digest: str) -> bool:
    """True (and one reuse spent) when *digest* may be served without a dump."""
    row = _ROWS.get(str(serial))
    if row is None or row[0] != digest:
        return False
    if row[1] <= 0:
        _ROWS.pop(str(serial), None)
        return False
    row[1] -= 1
    step_timing.note(REUSED_NOTE)
    return True


def forget(serial: str) -> None:
    """Drop the row (a new enable, a stale verdict)."""
    _ROWS.pop(str(serial), None)
