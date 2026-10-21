"""Replay: wait, event-driven, while the system installer shows "Installing..."."""

from __future__ import annotations

from tools.mobile.action_consts import WAIT_UNTIL_MAX_S
from tools.mobile.replay.context import Context
from tools.mobile.replay.installer_screen import installing_text
from tools.mobile.replay.waits_text import _wait_until_text
from tools.mobile.replay.waits_types import TextWait


async def await_install(ctx: Context, screen: object) -> object:
    """*screen*, or the screen once the installer's "Installing..." text is gone.

    Polls by dump (no sleep) until the text leaves or one wait ceiling passes; a
    screen that is not an installer mid-install is returned untouched.
    """
    text = installing_text(screen)
    if not text:
        return screen
    want = TextWait(text, WAIT_UNTIL_MAX_S * 1000, True)
    _found, latest, _timed_out = await _wait_until_text(ctx, screen, want)
    ctx.wait_interruption = ""
    return latest if isinstance(latest, dict) else screen
