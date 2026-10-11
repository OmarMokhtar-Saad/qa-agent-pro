"""Moved from handlers (refactor P2): mobile_step."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
from contextvars import ContextVar
from typing import Any, Callable

from handlers import _common, mobile_gate
from tools.tool_briefs import MOBILE_BRIEF

logger = logging.getLogger("tools.mcp_handlers")


def _mobile_start_heartbeat(run_id: str, token: str) -> None:
    """Keep the run's lease fresh while the tester thinks. Never raises.

    Best-effort by design: the heartbeat is a courtesy to the OTHER chat, and a
    run must not fail because a background writer could not start.
    """
    try:
        from tools.mobile import heartbeat as mobile_heartbeat

        mobile_heartbeat.start(run_id, token)
    except Exception:  # pragma: no cover - defensive
        logger.debug("mobile heartbeat could not start", exc_info=True)


async def _mobile_resume_device_stage(run_id: str, *, budget: object = None) -> str:
    """``""`` to carry on, or the markdown a resumed run must be answered with.

    Two failures this closes: an emulator that died between chats made every
    adb call fail with a device error the tester could not act on, and a
    re-booted emulator can come back on a DIFFERENT serial -- so the preflight
    has to run again before a packet is produced against it.
    """
    from tools.mobile import render as mobile_render
    from tools.mobile.session_core import STATE_NO_AVD
    from tools.mobile.session_device import ensure_run_device
    from tools.mobile.session_finish import preflight_for
    from tools.mobile.session_runs import resolve

    ready = await ensure_run_device(run_id, budget=budget)
    if ready.get("error"):
        return "\u26a0\ufe0f " + str(ready["error"])[:300]
    state = ready.get("content") or {}
    if str(state.get("state") or "") == STATE_NO_AVD:
        # The name comes off our own manifest, but it is echoed into markdown,
        # so only AVD-name characters survive.
        gone = "".join(
            c for c in str(state.get("avd") or "") if c.isalnum() or c in "._-"
        )[:60]
        return (
            "The emulator this run used"
            + (" (`" + gone + "`)" if gone else "")
            + " is not on this machine any more, so this run cannot be re-booted "
            "on it. Set up an emulator as below, then start a new run.\n\n"
            + mobile_render.setup_guide_block(
                mobile_render.setup_guide(mobile_render.SETUP_NO_AVD)
            )
        )
    if str(state.get("state") or "") != "ready":
        return mobile_render.device_pending_block(state)
    if not state.get("rebooted"):
        return ""
    resolved = resolve(run_id)
    if resolved.get("error"):
        return "\u26a0\ufe0f " + str(resolved["error"])[:300]
    checked = await preflight_for(resolved.get("content") or {})
    if checked.get("error"):
        return "\u26a0\ufe0f " + str(checked["error"])[:300]
    from tools.mobile import preflight as mobile_preflight

    pf = checked.get("content") or {}
    if not pf.get("ok"):
        return mobile_render.preflight_block(pf, mobile_preflight.render(pf))
    return ""


async def _mobile_next(
    run_id: str,
    session_token: str,
    *,
    progress: _common.ProgressCb = None,
    past_gate: bool = False,
    budget: object = None,
    first_packet: bool = False,
) -> str:
    """Render the next packet, the gate, or the summary. One place, three states."""
    from tools.mobile import render as mobile_render
    from tools.mobile.session_core import (
        STATE_BUSY,
        STATE_GATE,
        STATE_REPORT,
        new_budget,
    )
    from tools.mobile.session_packets import next_packet

    await _common._emit(progress, "\U0001f4f1 Preparing the next step…")
    bounded = budget if budget is not None else new_budget()
    result = await next_packet(
        run_id, session_token, past_gate=past_gate, budget=bounded
    )
    if result.get("error"):
        return "⚠️ " + str(result["error"])[:400]
    body = result.get("content") or {}
    state = str(body.get("state") or "")
    if state == STATE_BUSY:
        return mobile_render.busy_block(
            run_id, str(body.get("tc_id") or ""), packet=body.get("packet")
        )
    if state == STATE_REPORT:
        return await _mobile_report_text(run_id, session_token, body)
    if state == STATE_GATE:
        resolved = body.get("resolved") or {}
        return mobile_render.gate_block(
            {
                "done": resolved.get("done"),
                "total": resolved.get("total"),
                "failed": resolved.get("failed"),
            }
        )
    return _mobile_packet_header(run_id, body, first_packet) + (
        await _mobile_packet_text(
            body.get("packet"), session_token, body.get("resolved"), budget=bounded
        )
    )


def _mobile_packet_header(run_id: str, body: dict, first_packet: bool) -> str:
    """The heading above a step packet, with the reset note on a first packet."""
    resolved_body = (
        body.get("resolved") if isinstance(body.get("resolved"), dict) else {}
    )
    reset_note = ""
    if first_packet and (resolved_body.get("reset_app") or {}).get("cleared"):
        reset_note = (
            "**App data was already cleared at run start** -- do not add a "
            "`clear_app_data` action; the app is already in its post-reset "
            "state.\n\n"
        )
    return (
        reset_note
        + "## Mobile run `"
        + run_id
        + "`"
        + (" — " + str(body.get("tc_id")) if body.get("tc_id") else "")
        + "\n\nPlan the actions for the screen below and send them straight back "
        "with `qa_submit_mobile_step`.\n\n"
    )


def _mobile_render_report_file(run_id: str) -> tuple[str, bool, str]:
    """Render and open the report: ``(path, opened, failure_text)``. Never raises."""
    try:
        from tools.mobile import open_report as mobile_open
        from tools.mobile import report as mobile_report

        produced = mobile_report.render(run_id)
        if produced.get("error"):
            return "", False, str(produced["error"])
        path = str((produced.get("content") or {}).get("path") or "")
        opened = (mobile_open.open_report(path) or {}).get("content")
        return path, bool((opened or {}).get("opened")), ""
    except Exception:  # never-raise: a report is not a verdict
        logger.exception("mcp mobile report render failed")
        return (
            "",
            False,
            "the report renderer failed; the verdicts below are unaffected",
        )


async def _mobile_report_text(run_id: str, session_token: str, body: dict) -> str:
    """The reply for a run that is over: relay block, summary and report link."""
    from tools.mobile import render as mobile_render
    from tools.mobile.session_finish import finish_device
    from tools.mobile.session_records import reset_step_status, run_verdict
    from tools.mobile.session_runs import coverage_line, summary

    # The run is over: give the emulator back. THE HOLDER releasing its
    # own lock -- there is no reaper in this lane, and nothing here could
    # take a lock from another process even if it wanted to. Under the
    # lease this call is driving with, so a chat that has been displaced
    # since cannot free the new holder's device on its way out.
    await finish_device(run_id, lease=str(session_token or ""))
    _mobile_learn_after(run_id)
    listed = summary(run_id)
    # The SECOND consumer of the verdict-coverage value, and the one a
    # tester reads to answer "did this run pass": ``summary_block``'s tally
    # would otherwise let a STATUS stand in for a VERDICT. ``resolved`` is
    # carried by BOTH STATE_REPORT paths (``session.next_packet``).
    resolved = body.get("resolved")
    rows_for_relay = listed.get("content") or []
    coverage = coverage_line(
        resolved if isinstance(resolved, dict) else {}, rows_for_relay
    )
    report_path, report_opened, report_failed = _mobile_render_report_file(run_id)
    relay = mobile_render.relay_block(
        verdict=run_verdict(resolved, rows_for_relay),
        reset=reset_step_status(resolved, rows_for_relay),
        typed_field_line=mobile_render.typed_field_tally(rows_for_relay),
    )
    return relay + mobile_render.summary_block(
        rows_for_relay,
        coverage_line=coverage,
        run_id=run_id,
        # ``partial`` stays a hardcoded LIFECYCLE fact: this is reached only
        # when ``next_packet`` reports STATE_REPORT. It decides ONE word of the
        # heading and claims nothing about verdicts (the coverage line does).
        partial=False,
        report_path=report_path,
        report_opened=report_opened,
        report_error=report_failed,
    )


def _parse_tester_inputs(tester_inputs: str) -> dict:
    """The tester's per-field values as a dict; anything unparseable is ``{}``."""
    if not tester_inputs:
        return {}
    try:
        loaded = json.loads(tester_inputs)
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _forget_briefing_if_needs_model(run_id: str, case: dict) -> None:
    """Forget the run's briefing when the replay needed the model.

    A script this server could not parse is the observable signal that the
    model no longer has the static block in front of it -- a compacted chat, or
    one that never received it. Forgetting the briefing is what makes the
    omission self-healing: the very next packet carries the whole block again.
    Constraint 5 of the contract."""
    from tools.mobile import case_runner as _case_runner
    from tools.mobile import run_store

    if str(case.get("status") or "") != _case_runner.NEEDS_MODEL:
        return
    run_store.clear_briefing(run_id)
    run_store.clear_screen(run_id)
    # A `visual` assert stopped this replay (S1): the model asked to judge THIS
    # screen, so the next packet carries its picture. The LAST entry only -- the
    # trace is merged across submits.
    _trace = case.get("trace") if isinstance(case.get("trace"), list) else []
    _want = _MOBILE_SHOT_WANT.get()
    if (
        _trace
        and isinstance(_trace[-1], dict)
        and _trace[-1].get("outcome") == "visual_check"
        and isinstance(_want, dict)
    ):
        _want["visual"] = True


async def handle_submit_mobile_step(
    run_id: str,
    tc_id: str = "",
    script: str = "",
    tester_input: str = "",
    tester_input_field: str = "",
    session_token: str = "",
    *,
    confirm_destructive: bool = False,
    tester_inputs: str = "",
    progress: _common.ProgressCb = None,
) -> str:
    """Replay the actions YOU planned, then hand back the verdict and next packet.

    ``tester_input`` is a value the TESTER typed in chat for one named field. It
    is typed to the device through the IME on stdin and goes nowhere else: not
    into the reply, the trace, the checkpoint or the audit row. The audit detail
    is built by ``session.audit_detail``, which has NO parameter that could
    carry a value -- ``audit_log.record_event`` writes ``detail`` verbatim, and
    ``run_store.redact`` alone would not mask a value under a field name the
    tester chose.
    """
    # tools.untrusted, not agents.api_test_agent -- see handle_mobile_test.
    # Note this import precedes the lane check below, so an unshipped module
    # here takes out the REFUSAL path too, not just the working one.
    from tools.untrusted import single_line as _safe

    if not _common._mobile_lane_enabled():
        return mobile_gate._mobile_lane_off_message()

    call = {
        "tc_id": tc_id,
        "script": script,
        "tester_input": tester_input,
        "tester_input_field": tester_input_field,
        "tester_inputs": tester_inputs,
        "confirm_destructive": confirm_destructive,
    }
    try:
        return await _mobile_step_timed(run_id, session_token, call, progress)
    except Exception as exc:  # never-raise contract
        logger.exception("mcp submit_mobile_step failed")
        _common._capture_error(exc, "qa_submit_mobile_step")
        return "⚠️ That step could not be replayed: " + _safe(str(exc), 200)


async def _mobile_step_timed(
    run_id: str, session_token: str, call: dict, progress: _common.ProgressCb
) -> str:
    """Start the step budget and timing, acquire the run, then replay."""
    from tools.mobile import step_timing
    from tools.mobile.session_core import (
        new_budget,
    )

    # Created here, so the time the replay itself spends counts against the
    # budget the NEXT packet is produced under.
    budget = new_budget()
    # Fix round 2, item 4: arm the per-phase breakdown for this call.
    step_timing.begin()
    early, token = await _mobile_step_acquire(run_id, session_token)
    if early is not None:
        return early
    return await _mobile_step_run(run_id, token, call, budget, progress)


async def _mobile_step_run(
    run_id: str, token: str, call: dict, budget: Any, progress: _common.ProgressCb
) -> str:
    """Replay the script, audit it, and build the verdict plus the next packet."""
    from tools.mobile import render as mobile_render
    from tools.mobile import run_store, step_timing
    from tools.mobile.session_submit import submit
    from tools.untrusted import single_line as _safe

    tc_id = call["tc_id"]
    parsed_inputs = _parse_tester_inputs(call["tester_inputs"])
    previous_screen_id = str(
        (run_store.latest_screen(run_id) or {}).get("screen_id") or ""
    )
    result = await step_timing.timed(
        "replay",
        submit(
            run_id,
            tc_id,
            call["script"],
            session_token=token,
            tester_input=call["tester_input"],
            tester_input_field=call["tester_input_field"],
            tester_inputs=parsed_inputs,
            confirm_destructive=call["confirm_destructive"],
            budget=budget,
            **_mobile_route_kwargs(),
        ),
    )
    if result.get("error"):
        return "⚠️ " + _safe(result["error"], 400)
    body = result.get("content") or {}
    case = body.get("case") or {}
    await _mobile_step_audit(run_id, call, parsed_inputs, case)
    line = mobile_render.verdict_line(
        case,
        screen=run_store.latest_screen(run_id),
        previous_id=previous_screen_id,
    )
    notice = str(body.get("notice") or "")
    if body.get("packet"):
        tail = await _mobile_packet_text(
            body["packet"], token, body.get("resolved"), budget=budget
        )
    else:
        tail = await _mobile_next(run_id, token, progress=progress, budget=budget)
    reply = line + (("\n\n" + notice) if notice else "") + "\n\n" + tail
    return reply + _mobile_step_footer(run_id, tc_id, body)


async def _mobile_step_audit(
    run_id: str, call: dict, parsed_inputs: dict, case: dict
) -> None:
    """Note the case, write the audit row, forget the briefing if needed."""
    from tools.mobile.session_audit import audit_detail

    _note_mobile_step_case(case)
    await _common._audit(
        "mcp_mobile_step",
        entity_id=run_id,
        detail=audit_detail(
            run_id=run_id,
            tc_id=str(case.get("tc_id") or call["tc_id"]),
            verdict=str(case.get("verdict") or ""),
            status=str(case.get("status") or ""),
            field=str(call["tester_input_field"] or ""),
            fields=list(parsed_inputs.keys()) or None,
        ),
    )
    # A script this server could not parse is the observable signal that the
    # model no longer has the static block in front of it -- a compacted
    # chat, or one that never received it. Forgetting the briefing here is
    # what makes the omission self-healing: the very next packet carries the
    # whole block again, at the cost of the one turn that already went wrong
    # rather than of the run. Constraint 5 of the contract.
    _forget_briefing_if_needs_model(run_id, case)


async def _mobile_step_acquire(
    run_id: str, session_token: str
) -> tuple[str | None, str]:
    """Claim the run and take the device lock: ``(early_reply, token)``.

    ``early_reply`` is None when the step may go on.
    """
    from tools.mobile import render as mobile_render
    from tools.mobile import run_store
    from tools.mobile.device_lock import take_device_lock
    from tools.mobile.session_core import (
        claim,
    )
    from tools.mobile.session_finish import finish_device
    from tools.untrusted import single_line as _safe

    claimed = claim(run_id, session_token)
    if claimed.get("error"):
        return "⚠️ " + _safe(claimed["error"], 300), ""
    held = claimed.get("content") or {}
    if held.get("state") != run_store.HELD:
        # Displaced: give the device back in this call rather than
        # waiting for the heartbeat writer's next beat -- under THIS chat's
        # own lease, never as the holder, because this branch has just been
        # told it is not.
        await finish_device(
            run_id,
            lease=str((claimed.get("content") or {}).get("session_token") or ""),
        )
        return (
            mobile_render.takeover_block(
                run_store.takeover_message(run_id, str(held.get("holder") or "?"))
            ),
            "",
        )
    token = str(held.get("session_token") or "")
    _mobile_start_heartbeat(run_id, token)
    # THE REPLAY IS THE DEVICE-TOUCHING STEP. `session.submit` replays the
    # tester's action script through adb, so this entry takes the lock too
    # -- it is not enough that the call which produced the packet held it.
    # In the ordinary case this process already holds it for this run and
    # the acquire is REENTRANT: no syscall, no write. After a server restart
    # it acquires for real, which is what has to happen anyway.
    taken = (take_device_lock(run_id, lease=token) or {}).get("content") or {}
    if not taken.get("acquired"):
        return mobile_render.device_busy_block(taken), token
    _mark_mobile_step_held()
    return None, token


def _mobile_step_footer(run_id: str, tc_id: str, body: dict) -> str:
    """The timing line plus this device's dump-latency notes, ready to append."""
    from tools.mobile import dump_latency, run_store, step_timing

    # LAST, so the next packet's screenshot is inside the total. The same
    # line goes to the log, where the audit row's one duration_ms cannot
    # say which phase spent it.
    timing = step_timing.line()
    logger.info("mobile step %s %s: %s", run_id, tc_id, timing)
    run_store.append_step_timing(run_id, step_timing.current_row(0))
    # This device's dump latency and -- once per run -- the warning that
    # the host or the AVD is the bottleneck (fix round 3, item 2). The
    # serial is the run body's own, the one `_mobile_packet_text` reads.
    _res = body.get("resolved") if isinstance(body.get("resolved"), dict) else {}
    _serial = str(_res.get("serial") or "")
    slow: list = []
    if _serial:
        slow = [
            part
            for part in (
                dump_latency.describe(_serial),
                dump_latency.host_warning_once(run_id, _serial),
            )
            if part
        ]
    return "\n\n" + timing + "".join("\n" + part for part in slow)


#: THIS CALL's captured screens, as ``_image_content_blocks`` specs.
#:
#: A ContextVar rather than a module list, and that is not a style choice: two
#: chats can drive two runs through this process at once (which is why
#: ``mcp_server._inflight_enter`` exists), and a module-level accumulator would
#: attach one run's screen to the other run's reply. Each ``*_content`` entry
#: point below sets its OWN empty list, so a spec can only ever be read by the
#: call that produced it.
#:
#: ``None`` -- the default -- is a THIRD state and it is meaningful: it says
#: "this caller cannot carry image content", which is true of every caller of the
#: ``str``-returning names. ``_mobile_packet_text`` reads that as "do not even
#: ask the device", so the ~100 existing callers of those names pay no extra adb
#: round trip and see no behaviour change at all.
#:
#: WHY A SINK AT ALL, rather than returning tuples: the packet text is produced
#: deep inside ``_mobile_next`` and ``handle_submit_mobile_step``, behind roughly
#: forty ``return`` statements between them -- the lease check, the takeover
#: path, the device-lock refusal, the kill-switch refusal, the never-raise exit.
#: Threading a tuple back would have rewritten every one of those, for a value
#: only the outermost frame uses. ONE producer writes here and exactly two
#: readers drain it.
_MOBILE_IMAGE_SPECS: ContextVar = ContextVar("_MOBILE_IMAGE_SPECS", default=None)

#: The run-level ``env=`` the tester declared on THIS ``qa_mobile_test`` call,
#: parsed and validated. ``_mobile_hand_off`` stores it once in the manifest;
#: a ContextVar because the value has to cross frames that do not carry it.
_MOBILE_RUN_ENV: ContextVar = ContextVar("_MOBILE_RUN_ENV", default=None)

#: Strong references to in-flight learn tasks (the loop only keeps weak ones).
_MOBILE_LEARN_TASKS: set = set()


def _mobile_learn_after(run_id: str) -> None:
    """Schedule per-app learning for a finished run. Never raises, never waits."""
    try:
        if not run_id or not _common._mobile_lane_enabled():
            return
        from tools.mobile import knowledge_runner
        from tools.mobile.session_runs import resolve

        body = (resolve(run_id) or {}).get("content") or {}
        package = str(body.get("package") or "")
        if not package:
            return
        task = asyncio.get_running_loop().create_task(
            knowledge_runner.after_result(package, run_id)
        )
        _MOBILE_LEARN_TASKS.add(task)
        task.add_done_callback(_MOBILE_LEARN_TASKS.discard)
    except _MOBILE_TOOL_FAILURES:
        logger.exception("mcp mobile: could not schedule learning for %s", run_id)


#: Armed by ``handle_submit_mobile_step_content`` and marked by
#: ``handle_submit_mobile_step`` once the call HOLDS the run (claim, then the
#: device lock). The wrapper saves the call's ``note`` only when it was marked,
#: so a refused call (another chat's lease, a takeover, a busy device, a bad
#: run id) leaves no note behind. A ContextVar for the reason given above.
_MOBILE_STEP_HELD: ContextVar = ContextVar("_MOBILE_STEP_HELD", default=None)


def _note_mobile_step_case(case: object) -> None:
    """Hand the step's case to the wrapper that called it (a saved flow reads its trace).
    A no-op outside the wrapper."""
    held = _MOBILE_STEP_HELD.get()
    if isinstance(held, dict) and isinstance(case, dict):
        held["case"] = case


def _mark_mobile_step_held() -> None:
    """Record that this submit call holds its run. A no-op outside the wrapper."""
    held = _MOBILE_STEP_HELD.get()
    if isinstance(held, dict):
        held["held"] = True


def _mobile_route_kwargs() -> dict:
    """``{'route_expect': [...]}`` when the wrapper is replaying a saved route, else ``{}``.
    Conditional on purpose: a call that is not a route replay passes session.submit nothing new."""
    held = _MOBILE_STEP_HELD.get()
    expect = held.get("route_expect") if isinstance(held, dict) else None
    return {"route_expect": list(expect)} if expect else {}


#: What THIS call wants from the screenshot (S1, dump-first): ``{"asked": bool}``
#: from the tool's ``screenshot`` argument, plus ``"visual": True`` once the
#: submit path sees a ``visual`` assert stop. A dict, and mutated in place,
#: because the submit path and the packet renderer run in the same context.
#: ``None`` (every ``str``-returning caller) reads as "not asked".
_MOBILE_SHOT_WANT: ContextVar = ContextVar("_MOBILE_SHOT_WANT", default=None)

#: What replaces ``screen_block`` when the S8 memo says this chat already holds
#: the byte-identical list. The fingerprint lets a reader match the two.
_MOBILE_SAME_SCREEN_NOTE = (
    "SAME SCREEN as the last packet this chat was sent (fingerprint {fp}): the "
    "element list you already have is still exact, so it is not repeated. "
    "Plan from it."
)


async def _capture_and_pick_spec(
    serial: str, packet: object, body: dict, budget: object
) -> tuple:
    """``(note, spec)``: capture the screen, retrying a timeout once if the
    turn's budget has room. ``spec`` is ``None`` when nothing was captured."""
    from tools.mobile import adb as mobile_adb
    from tools.mobile import screenshot as mobile_screenshot
    from tools.mobile import step_timing

    note = mobile_screenshot.CAPTURE_FAILED_NOTE
    try:
        shot = await step_timing.timed("screenshot", mobile_screenshot.capture(serial))
        if (shot or {}).get("timed_out"):
            # At most ONE retry, and only when the turn's budget has room for it
            # (fix round 3, item 3): a first timeout already spent up to 30 s,
            # and a second full wait would overrun the call. The first timeout
            # is already marked on the call by `adb.screencap` or
            # `adb.screencap_raw`, so `_tracked` reports it as `timed_out`
            # whatever the retry does.
            wait = mobile_screenshot.retry_timeout_s(
                None if budget is None else budget.remaining(),
                mobile_adb.DEFAULT_TIMEOUT_S,
            )
            if wait is not None:
                shot = await step_timing.timed(
                    "screenshot", mobile_adb.screencap(serial, timeout_s=wait)
                )
            if (shot or {}).get("timed_out"):
                # Said as a TIMEOUT, not as the generic failure.
                note = mobile_screenshot.CAPTURE_TIMED_OUT_NOTE
        spec = mobile_screenshot.to_spec(
            (shot or {}).get("content"),
            run_id=body.get("run_id"),
            tc_id=(packet.get("tc_id") if isinstance(packet, dict) else ""),
        )
    except Exception:  # never-raise: a picture is not a verdict
        logger.exception("mcp mobile screen capture failed")
        spec = None
    return note, spec


def _store_mobile_shot(packet: object, body: dict, spec: dict) -> None:
    """Keep the picture, so the HTML report can show the screen rather than
    only a drawing of it. The SAME bytes the model was handed.

    Keyed on the packet's `observation_id` -- the look -- and never on a screen
    id, which is invariant across the turns of a chat. Best-effort exactly like
    `case_runner`'s `write_screen`: a failure is swallowed, because a lost
    picture may never cost the tester the packet."""
    try:
        from tools.mobile import run_store as mobile_run_store

        mobile_run_store.write_shot(
            str(body.get("run_id") or ""),
            str(packet.get("observation_id") or "") if isinstance(packet, dict) else "",
            spec.get("data"),
        )
    except Exception:  # never-raise: a picture is not a verdict
        logger.exception("mcp mobile screen store failed")


async def _mobile_capture_screen_note(
    packet: object, body: dict, attach: bool, budget: object
) -> str:
    """Capture this turn's screen and return the note that says what happened;
    a captured picture is appended to the image sink when it is to be attached."""
    from tools.mobile import screenshot as mobile_screenshot

    sink = _MOBILE_IMAGE_SPECS.get()
    serial = str(body.get("serial") or "")
    if sink is None:
        # No image channel on this path. NOT an attempt that failed -- so no
        # device call is made and the note says exactly that.
        return mobile_screenshot.NO_IMAGE_CHANNEL_NOTE
    if not serial:
        # A run with no device yet (an explore turn before provisioning): there
        # is nothing to capture FROM.
        return mobile_screenshot.CAPTURE_FAILED_NOTE
    note, spec = await _capture_and_pick_spec(serial, packet, body, budget)
    if spec is None:
        return note
    _store_mobile_shot(packet, body, spec)
    if not attach:
        return mobile_screenshot.NOT_ATTACHED_NOTE
    sink.append(spec)
    return mobile_screenshot.ATTACHED_NOTE


def _mobile_screen_attach(packet: object) -> tuple[str, bool]:
    """(the packet's ``screen_unusable`` routing key, whether to attach the picture).

    DUMP FIRST (S1). The picture is still taken and stored every step -- the
    report shows it -- but it goes to the model only when the element list is
    not enough, or the model asked. A packet without the routing key (not
    made by a `mobile_run` builder) reads as unusable: the safe direction."""
    unusable = (
        str(packet.get("screen_unusable", "unknown") or "")
        if isinstance(packet, dict)
        else "unknown"
    )
    want = _MOBILE_SHOT_WANT.get()
    want = want if isinstance(want, dict) else {}
    return unusable, bool(unusable or want.get("asked") or want.get("visual"))


async def _mobile_packet_text(
    packet: object,
    session_token: str,
    resolved: object,
    *,
    budget: object = None,
) -> str:
    """The rendered packet, PLUS this turn's screen captured and recorded.

    THE ONE PLACE either mobile tool turns a packet into text. Both call sites
    go through here so the picture, the note that says it is attached and the
    note that says it is not cannot disagree between the two tools -- and cannot
    drift, which is what a second copy of this logic at the other call site
    would do.

    ``resolved`` is the run body ``session.next_packet`` / ``session.submit``
    already carry on ``content["resolved"]``; ``serial`` is read from it rather
    than re-resolved, so this costs no extra disk read.

    Never raises and never loses the packet: a capture that fails, times out,
    or blows up leaves the note saying so and the packet otherwise untouched.
    """
    from tools.mobile import render as mobile_render

    body = resolved if isinstance(resolved, dict) else {}
    unusable, attach = _mobile_screen_attach(packet)
    note = await _mobile_capture_screen_note(packet, body, attach, budget)
    shown, brief_needed = packet, True
    if isinstance(shown, dict):
        shown, brief_needed = _mobile_shown_packet(
            shown,
            str(body.get("run_id") or ""),
            str(session_token or ""),
            note,
            unusable,
        )
    text = mobile_render.packet_block(shown, session_token=session_token)
    if brief_needed:
        text += "\n\n### Tool brief (sent once per chat)\n" + MOBILE_BRIEF.strip()
    return text


def _mobile_shown_packet(
    packet: dict, run_id: str, token: str, note: str, unusable: str
) -> tuple[dict, bool]:
    """``(copy_of_packet_to_show, brief_needed)`` with the memos applied."""
    brief_needed = True
    # A COPY: the packet is the caller's own dict, and a note written into
    # it would outlive this reply.
    shown = dict(packet)
    shown["screen_image"] = note
    # THE STATIC BLOCK, ONCE PER CHAT PER RUN. `render.STATIC_FIELDS` is
    # three fields that are byte-identical on every packet of every lane;
    # `response_schema` is NOT among them and never will be. The contract is
    # docs/RETIRED_CAPABILITIES.md section 5.
    #
    # BOTH ids are required. A packet we cannot attribute to a run AND a
    # chat is one whose model we cannot know has been briefed, and the safe
    # answer there is to repeat: a wasted block costs tokens, a missing one
    # costs the turn.
    if run_id and token:
        shown, brief_needed = _mobile_briefing_memo(shown, run_id, token)
        _mobile_screen_memo(shown, run_id, token, unusable=bool(unusable), note=note)
    # The observation key is this module's routing value, not packet
    # content: `render.packet_block` promises it adds nothing to what the
    # builder made, and a 25-character hex id in the model's JSON is packet
    # budget spent on a string the model has no use for. Popped from the
    # COPY only -- the caller's packet still carries it for the writer above.
    shown.pop("observation_id", None)
    shown.pop("screen_unusable", None)
    return shown, brief_needed


def _mobile_briefing_memo(shown: dict, run_id: str, token: str) -> tuple[dict, bool]:
    """``(shown, brief_needed)`` after the once-per-chat static-block memo."""
    from tools.mobile import render as mobile_render
    from tools.mobile import run_store as mobile_run_store

    brief_needed = True
    try:
        briefed = str(
            (mobile_run_store.briefed_session(run_id) or {}).get("content") or ""
        )
        if briefed == token:
            # The goal rides the same memo (fix round 3, item 1): a
            # chat that has been briefed has also seen the goal, and a
            # re-armed briefing brings both back together.
            shown = mobile_render.without_goal(mobile_render.without_static(shown))
            # The once-per-chat tool brief rides the same memo. Every
            # other path (either id missing, memo failure) keeps
            # `brief_needed`, so the fail-open direction is REPEAT.
            brief_needed = False
        else:
            mobile_run_store.mark_briefed(run_id, token)
    except Exception:
        # NEVER-RAISE, and the fail-open direction is REPEAT. This
        # function's promise is that a packet always comes back; the
        # elision is an optimisation and an optimisation must never be
        # the reason a tester's model gets an error string where a
        # screen belongs. `shown` is left whole, so the block goes out
        # again -- tokens, not a lost turn.
        logger.exception("mcp mobile static-block memo failed")
    return shown, brief_needed


def _mobile_screen_memo(
    shown: dict, run_id: str, token: str, *, unusable: bool, note: str
) -> None:
    """S8: the SAME screen, to the SAME chat, at a NEW position, goes as
    one line. Byte-identical text is the test, so the elision is
    lossless; a re-issued packet (same position) always goes whole,
    as does an unusable screen or one whose picture is attached."""
    from tools.mobile import run_store as mobile_run_store
    from tools.mobile import screenshot as mobile_screenshot

    try:
        block = str(shown.get("screen_block") or "")
        fingerprint = hashlib.sha256(block.encode("utf-8")).hexdigest()[:12]
        position = hashlib.sha256(
            json.dumps(
                [
                    shown.get(key)
                    for key in (
                        "kind",
                        "tc_id",
                        "turn",
                        "escapes_used",
                        "trace",
                        "stopped_because",
                    )
                ],
                default=str,
            ).encode("utf-8")
        ).hexdigest()[:12]
        last = (mobile_run_store.last_screen(run_id) or {}).get("content") or {}
        if (
            block
            and not unusable
            and note != mobile_screenshot.ATTACHED_NOTE
            and last.get("session") == token
            and last.get("fingerprint") == fingerprint
            and last.get("position") != position
        ):
            shown["screen_block"] = _MOBILE_SAME_SCREEN_NOTE.format(fp=fingerprint)
        if block:
            mobile_run_store.mark_screen(run_id, token, fingerprint, position)
    except Exception:
        # Fail-open is REPEAT, like the briefing memo above.
        logger.exception("mcp mobile screen memo failed")


def _mobile_note_target(run_id: str = "", package: str = "") -> tuple:
    """``(package, run_id, refusal)`` for a saved-app-note call.

    A run id wins (its manifest names the app); otherwise a validated
    ``package``. Never raises.
    """
    rid = str(run_id or "").strip()
    if rid:
        try:
            from tools.mobile.session_runs import resolve

            found = resolve(rid)
            pkg = str(((found or {}).get("content") or {}).get("package") or "")
            if pkg:
                return pkg, rid, ""
        except Exception:
            logger.exception("mobile note: could not resolve run %r", rid[:40])
    pkg = str(package or "").strip()
    if pkg:
        from tools import device_manager as _note_dm

        if _note_dm.valid_package_name(pkg):
            return pkg, rid, ""
        return "", rid, "`package` is not an Android package name"
    return (
        "",
        rid,
        "pass `package` (the app's Android package name, or `app` when it is one) "
        "or a `run_id` so it is filed under the right app",
    )


def _mobile_call_secrets(
    tester_input: object = "", tester_inputs_json: object = ""
) -> list:
    """The values the tester supplied on THIS call, so a note that repeats one
    is refused. Never raises."""
    values: list = []
    if str(tester_input or "").strip():
        values.append(str(tester_input))
    try:
        raw = str(tester_inputs_json or "").strip()
        parsed = json.loads(raw) if raw else {}
        if isinstance(parsed, dict):
            values += [str(v) for v in parsed.values() if str(v).strip()]
    except ValueError:
        pass
    return values


def _mobile_held_values(run_id: object) -> list:
    """The values held for *run_id* right now. Never raises."""
    from tools.mobile import held_inputs

    return [str(v) for v in held_inputs.recall(str(run_id or "")).values()]


def _call_arg(fn: Callable, args: tuple, kwargs: dict, name: str) -> str:
    """*name*'s value in a call to *fn*, however it was passed (bound by the
    real signature, so a reordered handler cannot misread it); ``""`` when it
    is absent or the call does not bind. Never raises."""
    if name in kwargs:
        return str(kwargs.get(name) or "")
    try:
        bound = inspect.signature(fn).bind_partial(*args, **kwargs)
    except (TypeError, ValueError):
        return ""
    return str(bound.arguments.get(name) or "")


def _mobile_note_spec(note: object) -> dict | None:
    """The ``note`` parameter as a dict, or ``None`` when it is not a JSON object."""
    try:
        spec = json.loads(note) if isinstance(note, str) else note
    except ValueError:
        return None
    return spec if isinstance(spec, dict) else None


def mobile_note_intake(
    note: object = "",
    run_id: str = "",
    package: str = "",
    secrets: object = (),
) -> str:
    """Save the ``note`` parameter and return a line to prefix the reply with
    (``""`` when there is no note, or the lane is off). Never raises."""
    if not str(note or "").strip() or not _common._mobile_lane_enabled():
        return ""
    try:
        from tools.mobile import app_knowledge
        from tools.untrusted import single_line

        spec = _mobile_note_spec(note)
        if spec is None:
            return (
                "Note not saved: pass `note` as a JSON object like "
                '{"kind": "wait", "text": "...", "when": {"rid": "send"}, '
                '"then": {"until_rid": "reply"}}.\n\n'
            )
        pkg, rid, refusal = _mobile_note_target(run_id, package)
        if refusal:
            return "Note not saved: " + refusal + ".\n\n"
        saved = app_knowledge.add_note(
            pkg,
            spec.get("text", ""),
            spec.get("kind", ""),
            spec.get("when"),
            spec.get("then"),
            source_run=rid,
            secrets=secrets,
        )
        if saved.get("error"):
            return "Note not saved: " + single_line(saved["error"], 300) + "\n\n"
        made = saved["content"]
        line = "Saved app note #%s for `%s` (%s). " % (made["id"], pkg, made["kind"])
        scoped = "It is scoped to an environment and is saved, not applied yet: "
        scoped += "no step is changed by it today; "
        local = "It applies to this app's later steps on this machine; "
        line += scoped if (made.get("when") or {}).get("env") else local
        line += "`qa_mobile_notes` lists or retires it.\n" + app_knowledge.echo(made)
        if made.get("superseded"):
            line += "\nSuperseded: " + ", ".join("#%s" % n for n in made["superseded"])
        return line + "\n\n"
    except Exception:
        logger.exception("mobile note intake failed")
        return "Note not saved: internal error.\n\n"


_MOBILE_TOOL_FAILURES = (
    OSError,
    RuntimeError,
    TimeoutError,
    ValueError,
    LookupError,
    TypeError,
    AttributeError,
    ImportError,
)


def _mobile_tool_failed(exc: BaseException, tool: str) -> str:
    """The text a mobile tool returns when it fails; the cause is logged."""
    logger.exception("%s failed", tool)
    _common._capture_error(exc, tool)
    return (
        "\u26a0\ufe0f `"
        + tool
        + "` failed ("
        + type(exc).__name__
        + "). Check `qa_mobile_status` before retrying."
    )


def _mobile_unresolved(noun: str, res) -> str:
    """The question for a device or app pick that did not settle on one value."""
    from tools.mobile import app_tools
    from tools.untrusted import wrap_untrusted

    options = ", ".join(str(getattr(o, "key", o)) for o in tuple(res.options or ()))
    question = str(res.question or "") or ("Ask the user which " + noun + " to use.")
    body = question + ("\nOptions: " + options if options else "")
    return (
        "\u26a0\ufe0f The "
        + noun
        + " is not settled. Do not guess; ask the user. Nothing was touched.\n"
        + wrap_untrusted("mobile_pick", body, app_tools.MAX_TOOL_TEXT_CHARS)
    )
