"""Replay waits: the text, gone and idle waits."""

from __future__ import annotations

import time

from tools.mobile.replay.context import Context
from tools.mobile.replay.probe_gate import TEXT
from tools.mobile.replay.screen import _screen_has, _screen_hash
from tools.mobile.replay.waits_idle import probe_idle
from tools.mobile.replay.waits_poll import _wait_for
from tools.mobile.replay.waits_types import Probe, TextWait


async def _wait_until_text(ctx: Context, screen: object, want: TextWait) -> tuple:
    """Poll the screen until ``want.text`` appears (or, with ``want.gone``, is
    no longer on it) or the budget runs out.

    Returns ``(found, latest_screen, timed_out)``. ``want.ms`` is the caller's
    cap; ``0`` falls back to ``DEFAULT_WAIT_UNTIL_TEXT_S`` so a script that
    forgot to set it does not poll forever.

    WATCHED (B6/S16): a screen that gains a NEW system dialog, a foreign app, a
    crash/ANR or a device that stopped answering ends the wait at once with
    ``(False, screen, False)`` and the sentence in ``ctx.wait_interruption``.
    """
    text, ms, gone = want
    label = "waiting for " + repr(str(text)[:40]) + (" to go" if gone else "")

    def met(_previous: object, current: object) -> bool:
        return _screen_has(current, text) != gone

    return await _wait_for(ctx, screen, Probe(ms, met, label, TEXT))


async def _wait_until_idle(ctx: Context, screen: object, ms: int) -> tuple:
    """Idle by probe frames (:func:`probe_idle`), else by dumps (S17).

    Same return and interruptions as :func:`_wait_until_text`. A blank frame or
    a failed probe falls back to :func:`_wait_idle_dumps` on the budget left.
    """
    started = time.monotonic()
    found = await probe_idle(ctx, screen, ms)
    if found is not None:
        return found
    left = ms - int((time.monotonic() - started) * 1000) if ms else 0
    if ms and left <= 0:
        return False, screen, True
    return await _wait_idle_dumps(ctx, screen, left)


async def _wait_idle_dumps(ctx: Context, screen: object, ms: int) -> tuple:
    """Poll until two reads in a row are the same screen (S17).

    The screen in hand is NOT the first of the two: it may be a read taken
    mid-animation, so idle is only ever claimed from two polls.
    """

    def met(previous: object, current: object) -> bool:
        return previous is not None and _screen_hash(previous) == _screen_hash(current)

    return await _wait_for(
        ctx, screen, Probe(ms, met, "waiting for the screen to settle")
    )
