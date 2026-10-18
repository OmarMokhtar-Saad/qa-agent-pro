"""Auto-replay: a step may only run when the dialog state in front is the one it expects.

A permission answer (``Allow`` / ``Not Allow``) belongs on an open dialog; every other
step belongs on the app with no dialog over it. Tapping ``Skip`` under an open dialog,
or answering a dialog that is not there, is a different run than the one the flow
recorded, so the replay stops and hands back to the model. Works for any app. Pure.
"""

from __future__ import annotations

from typing import Any

from tools.mobile import kstg_flow_perm, kstg_popup_permission
from tools.mobile.replay.screen import system_dialog_package

DIALOG_OVER = "a system dialog is open over the screen; this step expects none"
NO_DIALOG = "no system dialog is open; this step answers one"


def answers_dialog(step: dict) -> bool:
    """Whether *step* taps an allow or a decline button of a permission dialog."""
    if step.get("op") not in ("tap", "tap_text", "tap_text_if_present"):
        return False
    decline = kstg_popup_permission.is_permission_decline(kstg_flow_perm.label(step))
    return decline or kstg_flow_perm.kind(step) == "perm"


def mismatch(step: dict, screen: Any) -> str:
    """Why *step* must not run on *screen* (dialog open vs closed), else ``""``."""
    op = str(step.get("op") or "")
    answering = answers_dialog(step)
    opened = bool(system_dialog_package(screen))
    if opened and not answering and not op.startswith("wait_"):
        return DIALOG_OVER
    if answering and not opened and op in ("tap", "tap_text"):
        return NO_DIALOG
    return ""


def guarded(gate: Any, template: dict, origin: list) -> Any:
    """*gate* (the screen-identity gate of a batch) that also refuses a dialog mismatch."""
    steps = template.get("steps") or []

    async def checked(index: int, screen: Any) -> tuple:
        latest, why = await gate(index, screen)
        return latest, why or mismatch(steps[origin[index]], latest)

    return checked
