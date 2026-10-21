"""Replay: wait, event-driven, while the system installer shows "Installing..."."""

from __future__ import annotations

import time

from tools.mobile.action_consts import WAIT_UNTIL_MAX_S
from tools.mobile.replay.context import Context
from tools.mobile.replay.installer_screen import installing_text
from tools.mobile.replay.waits_text import _wait_until_text
from tools.mobile.replay.waits_types import TextWait

# A big app installs for longer than one wait ceiling (M2); the system installer
# says "Installing..." the whole time, so the wait goes on, round by round, up to
# this total.
INSTALL_CEILING_S = 180


async def install_round(ctx: Context, screen: object, want: TextWait) -> tuple:
    """One wait for *want* (the text to go): ``(timed_out, latest screen)``."""
    _found, latest, timed_out = await _wait_until_text(ctx, screen, want)
    return timed_out, (latest if isinstance(latest, dict) else screen)


def _round_for(text: str, end: float) -> TextWait:
    """The wait of one round: a wait ceiling, cut to what is left before *end* (L-2)."""
    left_ms = int((end - time.monotonic()) * 1000)
    return TextWait(text, max(1000, min(WAIT_UNTIL_MAX_S * 1000, left_ms)), True)


def _going_on(ctx: Context, timed_out: bool, screen: object) -> bool:
    """True when the round ran out while the installer is still installing."""
    return timed_out and not ctx.wait_interruption and bool(installing_text(screen))


async def await_install(ctx: Context, screen: object) -> object:
    """*screen*, or the screen once the installer's "Installing..." text is gone.

    Polls by dump (no sleep), one wait ceiling per round, for as long as the
    installer still shows "Installing..." (``INSTALL_CEILING_S`` in all, the last round cut to what is left); a
    screen that is not an installer mid-install is returned untouched.
    """
    text = installing_text(screen)
    if not text:
        return screen
    end = time.monotonic() + INSTALL_CEILING_S
    timed_out, latest = await install_round(ctx, screen, _round_for(text, end))
    while _going_on(ctx, timed_out, latest) and time.monotonic() < end:
        timed_out, latest = await install_round(ctx, latest, _round_for(text, end))
    ctx.wait_interruption = ""
    return latest
