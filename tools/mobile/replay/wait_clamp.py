"""Replay: say so when a wait asked for more than the 20 s cap (H03).

``max_s`` above ``WAIT_UNTIL_MAX_S`` is clamped by the model, not refused (a
refusal cost a whole script); the reply names what was asked and what ran.
"""

from __future__ import annotations

from tools.mobile.action_consts import WAIT_UNTIL_MAX_S


def note_clamp(entry: dict, action: object) -> None:
    """Stamp ``entry`` and its detail when *action* asked for a longer ``max_s``."""
    asked = getattr(action, "_asked_s", None)
    if not asked or asked <= WAIT_UNTIL_MAX_S:
        return
    note = "max_s %g clamped to %g." % (asked, WAIT_UNTIL_MAX_S)
    entry["max_s_clamped"] = note
    entry["detail"] = (str(entry.get("detail") or "") + " " + note).strip()
