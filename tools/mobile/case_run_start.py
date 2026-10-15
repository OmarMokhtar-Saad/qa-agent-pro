"""The start of a case: clean launch, wait for the app, first planning packet."""

from __future__ import annotations

import asyncio
import logging
import time

from tools.mobile import adb, run_store, step_timing
from tools.mobile import run_packets as mobile_run
from tools.mobile.case_run_counts import case_view, escapes_used
from tools.mobile.case_run_evidence import _abandon, _begin_evidence
from tools.mobile.case_run_planning import _planning_payload, _planning_record
from tools.mobile.case_run_types import _Opened, _Plan, _Ref, _Settle
from tools.mobile.case_run_wait import (
    FOREGROUND_POLL_S,
    FOREGROUND_TIMEOUT_S,
    MAX_FOREGROUND_POLLS,
    _foreground_refusal,
    _start_refusal,
)
from tools.mobile.providers import composite
from tools.mobile.replay.context import Context
from tools.mobile.replay.dump import dump_raw, resolve_activity

# Same object as case_run_consts.logger (getLogger is a registry); a local
# binding lets ruff see the handler below as logged (BLE001).
logger = logging.getLogger("tools.mobile.case_runner")


async def sleep(seconds: float) -> None:
    """Named so a test can replace it; a real sleep is the slowest thing here."""
    await asyncio.sleep(seconds)


def _now() -> float:
    """The wall clock, read at call time so a patched ``time`` is honoured."""
    return time.time()


# ``{"error", "content": {"tc_id", "screen", "packet", "escapes"}}``.
async def start_case(run_id: str, case: object, ctx: Context) -> dict:
    """Clean-launch the app, dump the first screen, build the planning packet."""
    step_timing.begin()
    try:
        return await _start(run_id, case, ctx)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.case_runner.start_case failed")
        return {"error": str(exc), "content": None}


async def _start(run_id: str, case: object, ctx: Context) -> dict:
    """Refuse, stop, launch; then plan against the first screen."""
    view = case_view(case)
    tc_id = view.get("tc_id") or ""
    refusal = _start_refusal(tc_id, ctx)
    if refusal:
        return refusal

    # Invariant 1. Unconditional, and in this order.
    stopped = await adb.force_stop(ctx.serial, ctx.package)
    if stopped.get("error"):
        return stopped
    evidence = await _begin_evidence(run_id, tc_id, ctx)
    launched = await step_timing.timed("launch", adb.launch(ctx.serial, ctx.package))
    if launched.get("error"):
        return await _abandon(_Ref(run_id, tc_id, ctx), evidence, launched)
    return await _plan_first_screen(run_id, ctx, _Opened(tc_id, view, evidence))


async def _plan_first_screen(run_id: str, ctx: Context, opened: _Opened) -> dict:
    """Wait for the app, resolve the screen, write the planning checkpoint."""
    settle = await step_timing.timed("foreground_wait", _wait_for_foreground(ctx))
    if settle.error:
        ref = _Ref(run_id, opened.tc_id, ctx)
        return await _abandon(ref, opened.evidence, settle.error)
    screen = await step_timing.timed("first_settle", _settled_screen(ctx, settle))
    planned = _write_planning(run_id, opened, screen)
    run_store.append_step_timing(run_id, step_timing.current_row(0))
    return planned


# THE SETTLE PRUNE passes NO activity on purpose: it answers ONE question --
# "is the app in front yet?" -- inside a loop that exists BECAUSE the answer
# is often no. `replay.dump.resolve_activity` MEMOISES what it probes, so
# probing from in here would freeze whatever held focus mid-launch (the
# launcher, an IME, a permission dialog) onto the context. The identity is
# resolved once, after the loop. The density is a DEVICE fact like the size,
# so the accessibility audit measures 48dp against THIS device.
async def observe_settle(ctx: Context) -> _Settle:
    """One dump pruned WITHOUT an activity (``in_front`` is not decided here)."""
    dumped = await dump_raw(ctx.serial)
    if dumped.get("error"):
        return _Settle(dumped, dumped, {}, {}, {}, False)
    sized = await adb.display_size(ctx.serial)
    dpi = await adb.display_density(ctx.serial)
    pruned = composite.observe(
        dumped.get("content"),
        "",
        composite.ScreenView(sized.get("content"), dpi.get("content")),
    )
    if pruned.get("error"):
        return _Settle(pruned, dumped, sized, dpi, {}, False)
    return _Settle(None, dumped, sized, dpi, pruned.get("content") or {}, False)


# `adb.launch` returns when the intent is delivered, not when the app is
# drawn, so the first dump used to race it. The budget is THE WALL CLOCK,
# not the sum of the sleeps (each dump costs 15-40s on a real device). It
# reads the MODULE-LEVEL `time`, so `monkeypatch.setattr(case_runner, "time",
# ...)` is the seam that pins the deadline. That seam moved: patch
# `case_run_start` now.
#
# Inside the loop: an empty package is a dump that named none -- accepted, not
# waited out. TWO exits, and only ONE establishes the app is in front: "the
# loop ended" and "the app is up" are two facts. And TWO bounds, and the
# second is not redundant: the first is only as honest as the clock it reads.
# See MAX_FOREGROUND_POLLS.
async def _wait_for_foreground(ctx: Context) -> _Settle:
    """WAIT FOR THE APP TO BE IN FRONT; ``error`` is set when it never was."""
    deadline = time.monotonic() + FOREGROUND_TIMEOUT_S
    polls = 0
    while True:
        seen_now = await observe_settle(ctx)
        if seen_now.error:
            return seen_now
        seen = str(seen_now.screen.get("package") or "")
        if not seen or seen == ctx.package:
            return seen_now._replace(in_front=seen == ctx.package)
        if time.monotonic() >= deadline or polls >= MAX_FOREGROUND_POLLS:
            refusal = {"error": _foreground_refusal(seen, ctx.package), "content": None}
            return seen_now._replace(error=refusal)
        polls += 1
        await sleep(FOREGROUND_POLL_S)


# THE IDENTITY, resolved once, and ONLY when the app was in front.
#
# ONE producer, shared with the replay -- see `replay.dump.resolve_activity`.
# The dump is re-pruned rather than re-fetched. Not resolving is the SAFE
# answer: the other exit is a dump that named no package, where the focused
# window may still be what the launch was moving away from, and
# `resolve_activity` memoises. A failure to resolve or re-prune leaves the
# settle screen standing -- never no screen at all.
async def _settled_screen(ctx: Context, settle: _Settle) -> dict:
    """The screen the planner is shown, identity resolved when the app is up."""
    activity = await resolve_activity(ctx) if settle.in_front else ""
    if not activity:
        return settle.screen
    settled = composite.observe(
        settle.dumped.get("content"),
        activity,
        composite.ScreenView(settle.sized.get("content"), settle.dpi.get("content")),
    )
    if settled.get("error"):
        return settle.screen
    return settled.get("content") or settle.screen


def _write_planning(run_id: str, opened: _Opened, screen: dict) -> dict:
    """Store the screen and the planning checkpoint; return the packet payload."""
    # The report joins a trace's screen ids to this library. A failure
    # here may never change a verdict, so the result is deliberately
    # not read: a lost wireframe beats a lost verdict.
    run_store.write_screen(run_id, screen)

    used = escapes_used(run_id, opened.tc_id)
    packet = mobile_run.build_case_job(
        opened.view,
        screen,
        mobile_run.CaseRef(run_id=run_id, tc_id=opened.tc_id, escapes=used),
    )
    plan = _Plan(used, time.time(), packet)
    run_store.write_case(run_id, opened.tc_id, _planning_record(opened, screen, plan))
    return _planning_payload(opened, screen, plan)
