"""Replay: how long an OPTIONAL tap looks for its text.

An optional control (a consent prompt, a promo) is usually absent, and an absent
one used to cost its whole ``max_s``. The answer is known as soon as the screen
stops changing: a text that is not on a screen that has settled will not appear
by waiting. So the wait ends on whichever comes first -- the text shows, the
screen proves settled (two reads alike, or an earlier step already proved this
very screen calm and nothing was actuated since), or ``max_s`` runs out for a screen
that keeps changing. No fixed sleep: it is the watched poll in ``waits_poll``.
"""

from __future__ import annotations

from tools.mobile.replay.context import Context, _ReplayState
from tools.mobile.replay.screen import _screen_has, _screen_hash
from tools.mobile.replay.waits_poll import _wait_for
from tools.mobile.replay.waits_prior import calm_for, note_calm
from tools.mobile.replay.waits_types import Probe, TextWait


def _calm_before(ctx: Context, state: _ReplayState) -> bool:
    """True when this very screen was already proven settled (an idle, gone or
    anchor wait, or an earlier absent optional tap) and nothing was actuated since."""
    return calm_for(ctx, _screen_hash(state.screen))


async def _watch_text_or_calm(ctx: Context, screen: object, want: TextWait) -> tuple:
    """``(found, screen, calm)``: poll until the text shows or two reads agree."""
    calm: list = []

    def met(previous: object, current: object) -> bool:
        if _screen_has(current, want.text):
            return True
        same = previous is not None and _screen_hash(previous) == _screen_hash(current)
        if same and _screen_hash(current):
            calm.append(True)
        return bool(calm)

    label = "waiting for " + repr(str(want.text)[:40]) + " (optional)"
    found, latest, _timed_out = await _wait_for(ctx, screen, Probe(want.ms, met, label))
    return found and _screen_has(latest, want.text), latest, bool(calm)


async def present_read(ctx: Context, state: _ReplayState, at: tuple) -> tuple:
    """``(found, screen, interruption)`` for one optional tap; *at* is
    ``(step index, TextWait)``. The wait's interruption sentence is read here."""
    _index, want = at
    if _calm_before(ctx, state) and not _screen_has(state.screen, want.text):
        return False, state.screen, ""
    ctx.wait_interruption = ""
    found, screen, calm = await _watch_text_or_calm(ctx, state.screen, want)
    interruption, ctx.wait_interruption = ctx.wait_interruption, ""
    if calm and not found and not interruption:
        note_calm(ctx, _screen_hash(screen))
    return found, screen, interruption
