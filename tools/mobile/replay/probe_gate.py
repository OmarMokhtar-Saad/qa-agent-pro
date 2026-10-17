"""Replay waits: gate real dumps behind the cheap screen probe.

A condition wait used to dump every poll (2.3-3.3 s each on a phone). A
:class:`Gate` holds one probe reading of the screen in hand and waits, with the
usual poll sleep, until a later reading differs; only then is a dump worth
paying for. Any probe failure reads as "moved" (after the sleep), so a broken
probe degrades to today's dump-per-poll, never to a wrong "unchanged".
"""

from __future__ import annotations

import time

from tools.mobile import dump_latency, screen_anchor, screen_probe, step_timing
from tools.mobile.replay.context import Context, _sleep
from tools.mobile.replay.context_consts import WAIT_POLL_S
from tools.mobile.replay.screen import _screen_hash

PROBE_BUDGET_S = 3.0
#: ``Probe.gate`` modes.
TEXT = "text"  # skip dumps until the frame moves; one confirming dump at the end
#: Verdicts after whose dump the gate is re-anchored and keeps gating.
KEEPS_GATE = ("dump", "unsure")
CALM = "calm"  # a screen provably current and quiet for one poll is calm


async def wait_moved(ctx: Context, base: screen_probe.Reading, until: float) -> object:
    """Poll the probe until it differs from *base*: the new reading, else ``None``.

    ``None`` means *until* passed with the frame unmoved. An unreadable probe
    returns an empty reading (differs, focus unknown) so the caller dumps.
    """
    while time.monotonic() < until:
        await _sleep(min(WAIT_POLL_S, max(0.0, until - time.monotonic())))
        left = until - time.monotonic()
        now = await screen_probe.read(ctx.serial, min(PROBE_BUDGET_S, max(left, 0.5)))
        if now is None:
            return screen_probe.Reading("", "")
        if not screen_probe.same(base, now):
            return now
    return None


def _quiet_trusted(ctx: Context, screen: object) -> bool:
    """True when *screen* is the last dump's and nothing actuated since."""
    return screen_anchor.screen_is_current(ctx.serial, _screen_hash(screen))


class Gate:
    """A probe baseline plus the poll window it may skip dumps in."""

    def __init__(self, ctx: Context, base: screen_probe.Reading, mode: str) -> None:
        self.ctx, self.base, self.mode = ctx, base, mode
        #: True until the first verdict: the baseline was bracketed by the dump
        #: in hand a moment ago, so a calm gate answers from that dump at once.
        self.primed = False
        #: True while ``base`` equals the "before" reading of the dump in hand;
        #: only then may a quiet poll vouch for that dump (calm mode).
        self.bracketed = True

    async def rebase(self, shown: object) -> bool:
        """Re-anchor on the screen just dumped; False when the probe is unusable.

        Calm mode skips dumps on the strength of one quiet poll, so only a
        baseline equal to the reading taken beside that dump may vouch for it.
        When the screen moved while that dump ran, a plain baseline still gates
        the next polls (``bracketed`` False: they may dump, never go calm), so
        one busy dump never drops the gate for the rest of the wait.
        """
        base = await _baseline(self.ctx, shown, self.mode)
        self.bracketed = base is not None
        base = base or await screen_probe.baseline(self.ctx.serial, PROBE_BUDGET_S)
        self.base = base or self.base
        return base is not None

    async def hold(self, until: float) -> str:
        """``"quiet"`` after one quiet poll (calm mode), ``"idle"`` when *until*
        passed unmoved, ``"moved"`` when a dump is due."""
        calm = self.mode == CALM
        horizon = min(until, time.monotonic() + WAIT_POLL_S) if calm else until
        if await wait_moved(self.ctx, self.base, horizon) is not None:
            return "moved"
        return "quiet" if calm else "idle"

    async def verdict(self, current: object, deadline: float) -> str:
        """``"calm"``: treat *current* as read twice. ``"dump"``: the frame moved,
        dump then re-anchor. ``"unsure"``: a quiet poll that cannot vouch for the
        screen in hand; dump, then re-anchor. ``"drop"``: the window is spent;
        dump now and stop gating, so that dump is the confirming read."""
        until = deadline - dump_latency.p90_ms(self.ctx.serial) / 1000.0
        if time.monotonic() >= until:
            return "drop"
        trusted = self._trusts(current)
        primed, self.primed = self.primed and trusted, False
        held = "quiet" if primed else await self.hold(until)
        if held == "moved":
            return "dump"
        if held == "idle":
            return "drop"
        if not (trusted and self._trusts(current)):
            return "unsure"
        step_timing.mark("gate_skip")
        return "calm"

    def _trusts(self, current: object) -> bool:
        """True when a quiet poll may vouch for *current* (calm mode only)."""
        return (
            self.mode == CALM and self.bracketed and _quiet_trusted(self.ctx, current)
        )


async def _baseline(ctx: Context, screen: object, mode: str) -> object:
    """The probe baseline for *screen*. Calm mode needs one bracketed by the
    "before" reading of the dump that showed it (that dump's own window is
    closed); text mode takes a plain one, its confirming dump covers the window."""
    if mode == CALM:
        return await screen_anchor.trusted_baseline(
            ctx.serial, _screen_hash(screen), PROBE_BUDGET_S
        )
    return await screen_probe.baseline(ctx.serial, PROBE_BUDGET_S)


async def open_gate(ctx: Context, screen: object, mode: str) -> Gate | None:
    """A gate for a wait in *mode*, or ``None`` when the probe cannot be trusted."""
    if mode not in (TEXT, CALM) or screen_anchor.cheaper_to_dump(ctx.serial):
        return None  # a dump is cheaper than the probe: plain dump polling
    base = await _baseline(ctx, screen, mode)
    if not base:
        return None
    gate = Gate(ctx, base, mode)
    gate.primed = True
    return gate


async def rebased(gate: Gate | None, shown: object) -> Gate | None:
    """*gate* re-anchored on the screen just dumped, or ``None`` when unusable."""
    return gate if gate is not None and await gate.rebase(shown) else None
