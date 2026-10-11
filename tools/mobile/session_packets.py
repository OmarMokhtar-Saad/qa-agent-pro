"""Mobile session: next-packet and the final-packet capture. Cut verbatim from tools/mobile/session.py (P4.4)."""

from __future__ import annotations

import asyncio
import logging
import time

from tools.mobile import adb, case_runner, explore_runner, run_store
from tools.mobile.providers import composite
from tools.mobile.replay.context import Context
from tools.mobile.replay.dump import dump_raw
from tools.mobile.session_core import (
    LANE_EXPLORE,
    STATE_GATE,
    STATE_REPORT,
    STATE_RUNNING,
    Budget,
    _busy,
    _in_reserve,
)
from tools.mobile.session_explore import (
    _explore_abort_network,
    _explore_begin_network,
    _persist_explore,
)
from tools.mobile.session_runs import context_for, load_case, resolve
from tools.mobile_evidence import capture

logger = logging.getLogger("tools.mobile.session")


async def _final_packet(ctx: object, budget: object) -> dict | None:
    """One best-effort screen capture spent from the reserved tail of a busy
    reply. Never raises -- a device that will not answer in time degrades the
    busy reply to plain text, exactly as before this change.

    Reuses the SAME primitive ``case_runner.start_case`` uses to prune a fresh
    dump into a screen (``adb.uiautomator_dump``/``display_size``/
    ``display_density`` feeding :func:`composite.observe`), with no relaunch,
    no foreground poll and no activity resolve -- those are the slow parts
    this fix exists to avoid starting this close to the client's own timeout.

    The whole capture runs under ``asyncio.wait_for`` with what is left of
    *budget* as its timeout, so a device that stalls on any of the three adb
    calls is cut off at the budget deadline and the call never overruns
    :data:`CALL_BUDGET_S`.
    """
    serial = str(getattr(ctx, "serial", "") or "")
    remaining = getattr(budget, "remaining", None)
    if not serial or not callable(remaining):
        return None

    async def capture() -> dict | None:
        dumped = await dump_raw(serial)
        if dumped.get("error"):
            return None
        sized = await adb.display_size(serial)
        dpi = await adb.display_density(serial)
        pruned = composite.observe(
            dumped.get("content"),
            "",
            display=sized.get("content"),
            density=dpi.get("content"),
        )
        if pruned.get("error"):
            return None
        return pruned.get("content") or None

    try:
        return await asyncio.wait_for(capture(), timeout=max(0.0, remaining()))
    except asyncio.TimeoutError:
        logger.info("mobile.session: _final_packet capture hit the call budget")
        return None
    except Exception:  # pragma: no cover - defensive, see docstring
        logger.warning("mobile.session: _final_packet capture failed", exc_info=True)
        return None


async def next_packet(
    run_id: str,
    session_token: str = "",
    *,
    past_gate: bool = False,
    budget: Budget | None = None,
) -> dict:
    """The next thing the tester's model must answer, or a terminal state.

    ``{"error", "content": {"state", "packet", "tc_id", "resolved", ...}}``.

    ``past_gate`` is the tester having ALREADY answered the soft "keep going?"
    gate. It belongs here, at the one place the gate state is decided, because
    the gate was previously evaluated twice -- once in the handler, which
    honoured the override, and again in the renderer, which did not -- so a run
    past the gate looped on it forever. One decision, one answer.
    """
    try:
        resolved = resolve(run_id, session_token)
        if resolved.get("error"):
            return resolved
        body = resolved["content"] or {}
        ctx = context_for(body)
        if str(body.get("lane")) == LANE_EXPLORE:
            return await _next_explore_packet(run_id, body, ctx, budget)
        return await _next_suite_packet(run_id, body, ctx, past_gate, budget)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.session.next_packet failed")
        return {"error": str(exc), "content": None}


async def _next_explore_packet(
    run_id: str, body: dict, ctx: Context, budget: Budget | None
) -> dict:
    """The explore lane's next packet (one turn), or its busy/report answer."""
    state = body.get("explore") or {}
    if budget is not None and _in_reserve(budget):
        # Checked BEFORE the turn, never during: a turn dumps the screen
        # and writes state, and abandoning one half-done is worse than
        # answering with a pointer. The reserved tail is spent on ONE
        # best-effort capture instead of returning bare -- see
        # _final_packet.
        # Always attempt the capture: _final_packet self-guards with
        # asyncio.wait_for(..., timeout=max(0.0, remaining())), so an
        # already-expired budget degrades to None on its own instead
        # of skipping the attempt outright.
        return _busy(body, "", packet=await _final_packet(ctx, budget))
    turn = await explore_runner.next_turn(run_id, state, ctx)
    if turn.get("error"):
        return turn
    content = turn.get("content") or {}
    running = str(content.get("status") or "") == explore_runner.RUNNING
    if running:
        # WHEN THIS TURN BEGAN, in the same unit the suite lane uses for
        # a case. The checkpoint is written when the turn ENDS, so
        # without this the only instant it knows is the end and its
        # evidence window collapses to the pad around it.
        began = dict(content.get("state") or {})
        began["turn_started"] = time.time()
        content["state"] = began
    content["state"] = await _explore_turn_network(
        run_id, content, ctx, running=running, budget=budget
    )
    _persist_explore(run_id, content.get("state"))
    return {
        "error": None,
        "content": {
            "state": STATE_RUNNING if running else STATE_REPORT,
            "packet": content.get("packet"),
            "tc_id": "",
            "status": str(content.get("status") or ""),
            "resolved": body,
        },
    }


async def _explore_turn_network(
    run_id: str,
    content: dict,
    ctx: Context,
    *,
    running: bool,
    budget: Budget | None,
) -> object:
    """The turn's state after starting (or, once stopped, aborting) its capture."""
    # The wire, for this turn. Started HERE, at the packet build, for
    # the same reason the suite lane starts it before its launch: the
    # capture has to cover what the turn is about to do. A stopped
    # session, a physical device or a flag that is off all land as a
    # record which SAYS so, and none of them can block a turn.
    # The budget is re-read HERE and not only before the turn: the
    # capture adds two device round trips AFTER the only earlier check,
    # and on a slow emulator that is what pushes the packet build past
    # the client's timeout. Losing the capture costs a section that
    # says why; losing the call costs the turn.
    if running and not (budget is not None and budget.expired()):
        return await _explore_begin_network(run_id, content.get("state"), ctx)
    if not running:
        # The session has STOPPED. Any capture a previous packet build
        # started is now never going to be read, so it is stopped and
        # its file deleted here rather than left recording the tester's
        # device until something else displaces it.
        return await _explore_abort_network(run_id, content.get("state"), ctx)
    return content.get("state")


async def _next_suite_packet(
    run_id: str,
    body: dict,
    ctx: Context,
    past_gate: bool,
    budget: Budget | None,
) -> dict:
    """The suite lane's next packet: report, gate, busy or a started case."""
    if body.get("finished"):
        await _finish_evidence(run_id, body)
        return _suite_reply(STATE_REPORT, body, None, "")
    if body.get("gate") and not past_gate:
        return _suite_reply(STATE_GATE, body, None, str(body.get("next_tc_id") or ""))
    loaded = load_case(run_id, str(body.get("next_tc_id") or ""))
    if loaded.get("error"):
        return loaded
    if budget is not None and _in_reserve(budget):
        # start_case force-stops the app, relaunches it and dumps the screen.
        # With the budget gone that work would land after the client has
        # already given up on the call, so nothing is started at all. The
        # reserved tail is spent on ONE best-effort capture instead -- see
        # _final_packet.
        # Always attempt the capture -- see the matching comment in the
        # explore-lane busy branch above.
        return _busy(
            body,
            str(body.get("next_tc_id") or ""),
            packet=await _final_packet(ctx, budget),
        )
    started = await case_runner.start_case(run_id, loaded["content"], ctx)
    if started.get("error"):
        return started
    content = started.get("content") or {}
    return _suite_reply(
        STATE_RUNNING, body, content.get("packet"), str(content.get("tc_id") or "")
    )


def _suite_reply(state: str, body: dict, packet: object, tc_id: str) -> dict:
    """The suite lane's next-packet reply shape."""
    return {
        "error": None,
        "content": {
            "state": state,
            "packet": packet,
            "tc_id": tc_id,
            "resolved": body,
        },
    }


async def _finish_evidence(run_id: str, resolved: object) -> None:
    """Pull the app's own event log ONCE, when a run reaches its report.

    Idempotent on the manifest: a run whose manifest already records an
    ``events_source`` is not pulled again, so a resumed chat that lands on the
    finished state a second time makes no adb call. ``capture.pull_events``
    itself skips, with a reason, when the package has no profile, the flag is
    off, or ``run-as`` is refused (a release build) -- and whatever it says is
    recorded so the report can show it (plan D8). Nothing here can change the
    run's state or a verdict: every exception ends in this function.
    """
    try:
        body = resolved if isinstance(resolved, dict) else {}
        manifest = (run_store.read_manifest(run_id) or {}).get("content") or {}
        if not isinstance(manifest, dict) or manifest.get("events_source"):
            return
        pulled = await capture.pull_events(
            str(body.get("serial") or manifest.get("serial") or ""),
            str(body.get("package") or manifest.get("package") or ""),
            run_id,
        )
        content = pulled.get("content") if isinstance(pulled, dict) else None
        content = content if isinstance(content, dict) else {}
        # RE-READ after the await: a state or lease write may have landed while
        # the device was being read, and the copy taken before the pull would
        # roll it back. Only the three evidence keys are merged into the fresh copy.
        fresh = (run_store.read_manifest(run_id) or {}).get("content")
        fresh = fresh if isinstance(fresh, dict) else manifest
        fresh["events_source"] = str(content.get("events_source") or "none")
        fresh["events_reason"] = str(
            content.get("reason")
            or (pulled.get("error") if isinstance(pulled, dict) else "")
            or ""
        )[:400]
        fresh["events_segments"] = content.get("segments")
        run_store.write_manifest(run_id, fresh)
    except Exception:  # never-raise: evidence is not a verdict
        logger.exception("mobile.session._finish_evidence failed")


async def finish_evidence(run_id: str, resolved: object) -> None:
    """The public face of :func:`_finish_evidence`, for callers outside this module.

    ``qa_mobile_status(report_now=True)`` renders a run's report from another chat
    and must pull the app's event log first, once; it goes through here so that a
    module boundary is crossed by a public name, not a private one. Same contract:
    idempotent on the manifest, never raises, never changes state or a verdict.
    """
    await _finish_evidence(run_id, resolved)
