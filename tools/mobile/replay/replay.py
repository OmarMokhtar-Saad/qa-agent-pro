"""Replay engine: the replay loop and its step dispatch. Cut verbatim from tools/mobile/executor.py (P4.5)."""

from __future__ import annotations

import asyncio
import logging
import time

from tools.mobile import actions as actions_mod
from tools.mobile import (
    ime,
    step_timing,
)
from tools.mobile.replay.context import (
    STATUS_DONE,
    STATUS_ERROR,
    STATUS_UNVERIFIED,
    Context,
    _append,
    _ReplayRun,
    _ReplayState,
    _result,
    _Step,
    _StepCursor,
)
from tools.mobile.replay.device_ops import (
    PASSWORD_REFUSAL_STATUS,
    _perform,
    keyboard_down,
    keyboard_up,
    needs_keyboard,
)
from tools.mobile.replay.dump import _first_screen
from tools.mobile.replay.elements import _clamp_source
from tools.mobile.replay.guard import NOTHING_MOVED_NOTE, _confirmed, inert_ops
from tools.mobile.replay.knowledge import (
    _apply_knowledge,
    _flush_knowledge,
    _read_knowledge,
)
from tools.mobile.replay.screen import (
    _entry,
    _screen_hash,
    _screen_id,
    _stamp_after,
    _texts,
    _verification_summary,
    _verified,
)
from tools.mobile.replay.settle import _settle
from tools.mobile.replay.steps import (
    _ask_tester_step,
    _assert_step,
    _budget_stop,
    _done_stop,
    _guard_stop,
    _knowledge_post,
    _knowledge_pre,
    _knowledge_stop,
    _knowledge_target,
    _resolve_element,
    _route_mismatch_stop,
    _stamp_screen_key,
)
from tools.mobile.replay.verdicts import (
    SCROLL_TARGET_MISSED,
    _brought_to_front,
    _budget_seconds,
    _route_expected,
)
from tools.mobile.replay.waits import _changed_wait, _condition_wait, _wait_spec

logger = logging.getLogger("tools.mobile.executor")


async def _begin_knowledge_run(ctx: Context) -> object:
    """Run facts for this replay (version, env), cached per run; ``None`` on any
    failure. Copies the version and env onto the Context."""
    if not getattr(ctx, "run_id", None):
        return None
    try:
        from tools.mobile import knowledge_run

        facts = await knowledge_run.begin_run(ctx)
        ctx.run_facts = facts
        ctx.app_version = facts.app_version
        ctx.env = dict(facts.env)
        return facts
    except Exception:
        logger.exception("mobile app knowledge: begin_run failed")
        return None


async def replay(script: object, ctx: Context) -> dict:
    """Execute *script*; the contract is :func:`_replay_keyboard`'s.

    Loads this app's saved notes ONCE (read-only) and flushes their buffered
    counters in a ``finally``, so success, error, exception and abort all
    flush. Both steps degrade to "no notes" and never raise, and both run in a
    worker thread so a busy SQLite file (up to its timeout) never blocks the loop.
    """
    try:
        # Inside the try: a cancel during the load still reaches the flush,
        # which drops ctx.knowledge. The worker only READS; the assignment
        # happens here, on the loop, so a cancelled load never assigns.
        if getattr(ctx, "knowledge", None) is None:
            package = str(getattr(ctx, "package", "") or "")
            facts = await _begin_knowledge_run(ctx)
            args = (package,) if facts is None else (package, facts)
            ctx.knowledge = await asyncio.to_thread(_read_knowledge, *args)
        return await _replay_keyboard(script, ctx)
    finally:
        await asyncio.to_thread(_flush_knowledge, ctx)


async def _replay_keyboard(script: object, ctx: Context) -> dict:
    """Execute *script* with the QA keyboard up for its typing.

    A script with a ``type``/``clear`` step runs :func:`keyboard_up` before its
    first step and :func:`keyboard_down` in a ``finally`` -- success, failure,
    an exception and a cancelled task (an abort) all restore. A keyboard that
    cannot come up stops the run by name before any step, restoring first if
    the select had already happened. Otherwise the contract is
    :func:`_replay_steps`'s.
    """
    if not needs_keyboard(script):
        return await _replay_steps(script, ctx)
    serial = str(getattr(ctx, "serial", "") or "")
    carried = str(getattr(ctx, "typing_fallback", "") or "")
    up = {"error": carried, "content": None} if carried else await keyboard_up(serial)
    previous = (up.get("content") or {}).get("previous")
    fallback_note = ""
    if up.get("error"):
        # R1d: a keyboard that cannot come up no longer STOPS the run. The tester's
        # keyboard is given back first (if the select had happened), the steps run
        # with typing over stdin, and the result says so, why, and the exact fix.
        detail = str(up["error"])[:400]
        if previous is not None:
            await keyboard_down(serial, str(previous))
        previous = None
        ctx.typing_fallback = detail
        fallback_note = ime.fallback_notice(detail, serial)
    down: dict = {"error": None, "content": None}
    try:
        replied = await _replay_steps(script, ctx)
    finally:
        if previous is not None:
            down = await keyboard_down(serial, str(previous))
    if fallback_note:
        note = fallback_note
    elif down.get("error"):
        note = str(down["error"])
    else:
        note = str((down.get("content") or {}).get("note") or "")
    content = replied.get("content") if isinstance(replied, dict) else None
    if note and isinstance(content, dict):
        content["keyboard"] = note
        content["reason"] = (str(content.get("reason") or "") + " " + note).strip()
    return replied


def _record_script_feedback(ctx: Context, script: object, screen: object) -> None:
    """``Script.knowledge_feedback`` into ``ctx.kbuf`` (in memory, step index 0).
    Never raises."""
    items = list(getattr(script, "knowledge_feedback", None) or [])
    if not items:
        return
    try:
        from tools.mobile import knowledge_feedback

        knowledge_feedback.record(ctx, items, screen, 0)
    except Exception:
        logger.exception("mobile app knowledge: script feedback failed")


async def _replay_open(
    ctx: Context, script: object, screen: object, trace: list
) -> tuple[object, dict | None]:
    """``_replay_first_screen``, then the script's feedback is recorded before any
    step can stop the run, so it is kept even when no step is dispatched."""
    items = list(getattr(script, "actions", None) or [])
    screen, reply = await _replay_first_screen(ctx, items, screen, trace)
    _record_script_feedback(ctx, script, screen)
    return screen, reply


async def _replay_first_screen(
    ctx: Context, items: list, screen: dict | None, trace: list[dict]
) -> tuple[dict | None, dict | None]:
    """The screen the first action sees, or the reply that ends the replay
    before it starts: ``(screen, reply)``, ``reply`` is ``None`` to go on."""
    if not items:
        return screen, {
            "error": None,
            "content": _result(
                STATUS_ERROR, trace, screen, "", "The script had no actions.", -1
            ),
        }
    if screen is None:
        dumped = await _first_screen(ctx)
        if dumped.get("error"):
            return screen, {
                "error": None,
                "content": _result(
                    STATUS_ERROR, trace, None, "", str(dumped["error"]), -1
                ),
            }
        screen = dumped.get("content")
    return screen, None


def _device_error_stop(
    run: _ReplayRun, outcome: dict, entry: dict, screen: object
) -> dict:
    """The reply for a device action that came back with an error."""
    if outcome.get("mask_text") and isinstance(entry.get("action"), dict):
        for key in ("text", "value"):
            if key in entry["action"]:
                entry["action"][key] = actions_mod.SECRET_MASK
    recoverable = bool(outcome.get("needs_model"))
    entry["outcome"] = "refused" if recoverable else "device_error"
    entry["detail"] = _confirmed(entry, str(outcome["error"]))[:400]
    _stamp_after(entry, screen)
    _append(run.trace, entry)
    return {
        "error": None,
        "content": _result(
            PASSWORD_REFUSAL_STATUS if recoverable else STATUS_ERROR,
            run.trace,
            screen,
            "",
            entry["detail"],
            entry["index"],
        ),
    }


async def _settle_device(
    run: _ReplayRun, step: _Step, outcome: dict, entry: dict, screen: object
) -> tuple[object, dict | None]:
    """Settle after a device action: ``(screen, reply)``.

    ``reply`` is ``None`` when the replay goes on to the next action.
    """
    op, action = step.op, step.action
    # A `launch` whose `am start` only brought this app's own task to
    # the front of a screen that was ALREADY this app changed nothing a
    # dump would see. The one redump it skips is the costliest call on a
    # heavy AVD. ONLY the dump is skipped: `_settle` still runs on the
    # screen in hand, so `no_change` and the system-dialog and
    # left-app checks are kept.
    front = op == "launch" and _brought_to_front(
        outcome, screen, run.ctx, named=getattr(action, "package", None)
    )
    if front:
        step_timing.mark("dump_skipped")
    if op in actions_mod.MUTATING_OPS:
        screen, stop = await _settle(
            run.ctx,
            entry,
            screen,
            run.trace,
            entry["index"],
            redump=not front,
            mark_no_change=True,
            op=op,
            extra_expected=(
                (str(getattr(action, "package", "") or ""),) if op == "launch" else ()
            ),
        )
        if stop is not None:
            return screen, {"error": None, "content": stop}
    _stamp_after(entry, screen)
    _append(run.trace, entry)
    return screen, None


async def _wait_step(
    run: _ReplayRun, step: _Step, screen: object
) -> tuple[object, dict | None]:
    """Run a wait op: ``(screen, reply)``, ``reply`` is ``None`` to go on.

    Every wait is a condition with a bound (S17): the legacy `wait` maps onto
    the same four through `_wait_spec`.
    """
    spec = _wait_spec(step.action, run.items)
    if spec[0] != "changed":
        return await _condition_wait(run, step.action, spec, step.entry, screen)
    return await _changed_wait(run, step.op, spec[2], step.entry, screen)


async def _device_step(
    run: _ReplayRun, step: _Step, screen: object
) -> tuple[object, dict | None]:
    """Run a device op: ``(screen, reply)``, ``reply`` is ``None`` to go on."""
    entry = step.entry
    # ONE rectangle for the guard and the device -- see
    # `_swipe_points`. Both read this packet's own root_bounds
    # rather than each asking adb, whose answer expires.
    outcome = await _perform(
        step.op, step.action, step.element, run.ctx, _clamp_source(screen)
    )
    if outcome.get("error"):
        return screen, _device_error_stop(run, outcome, entry, screen)
    entry["outcome"] = "ok"
    entry["detail"] = _confirmed(
        entry, str((outcome.get("content") or {}).get("detail") or "")
    )
    if step.scroll_missed:
        entry["detail"] = (SCROLL_TARGET_MISSED + " " + entry["detail"]).strip()
    return await _settle_device(run, step, outcome, entry, screen)


async def _dispatch_op(
    run: _ReplayRun, step: _Step, screen: object, baseline_texts: set | None
) -> tuple[object, dict | None]:
    """Run an action that passed the guard: an assert, a wait or a device op.

    ``(screen, reply)``; ``reply`` is ``None`` when the replay goes on.
    """
    if step.op == "assert":
        reply = _assert_step(run, step.action, step.entry, screen, baseline_texts)
        return screen, reply
    if step.op in actions_mod.WAIT_OPS:
        return await _wait_step(run, step, screen)
    return await _device_step(run, step, screen)


def _before_texts(op: str, screen: object, previous: set | None) -> set | None:
    """The BEFORE set for ``assert new_text``.

    Captured for every op that can change the screen -- ``wait`` included,
    which is the whole point: the reply a case is waiting for arrives during
    the wait. Any other op keeps the set it had.
    """
    baseline_texts = previous
    if op in actions_mod.MUTATING_OPS:
        baseline_texts = _texts(screen)
    return baseline_texts


async def _knowledge_before(
    run: _ReplayRun, cursor: _StepCursor, step_at: tuple, screen, deadline: float
) -> tuple:
    """Saved app notes for one step, BEFORE its target resolves.

    ``(screen, reply, inserted)``. A wait note polls (bounded) and an avoid
    note refuses -- ``reply`` is then the stop; ``inserted`` means a note put
    steps in front of this one and the loop must move to them.
    """
    index, action, entry = step_at
    op = str(getattr(action, "op", "") or "")
    screen, note_stop = await _apply_knowledge(run, step_at, op, screen, deadline)
    if note_stop is not None:
        return screen, _knowledge_stop(run, index, entry, screen, note_stop), False
    reply, inserted = await _knowledge_pre(run, cursor, step_at, screen, deadline)
    return screen, reply, inserted


async def _target_step(run: _ReplayRun, step_at: tuple, op: str, screen) -> tuple:
    """Resolve the step's target, then run the destructive guard (`_guard_stop`).

    ``(step, reply)``; ``reply`` is not None when resolution or the guard stops.
    """
    index, action, entry = step_at
    element, scroll_missed, reply = _resolve_element(run, op, action, entry, screen)
    element, reply = await _knowledge_target(
        run, entry, action, screen, (element, reply)
    )
    if reply is not None:
        return None, reply
    step = _Step(op, action, entry, element, scroll_missed)
    return step, _guard_stop(run, step, screen, index)


async def _replay_steps(script: object, ctx: Context) -> dict:
    """Execute *script* against ``ctx``. Never raises.

    ``{"error": None, "content": {"status", "trace", "screen", "verdict",
    "reason", "stopped_at"}}``. Top-level ``error`` is reserved for this
    function itself failing -- a device refusal is ``status == "error"`` with a
    trace, because the trace is the only thing that tells a tester how far the
    case got.
    """
    trace: list[dict] = []
    screen = ctx.screen if isinstance(ctx.screen, dict) else None
    try:
        items = list(getattr(script, "actions", None) or [])
        screen, reply = await _replay_open(ctx, script, screen, trace)
        if reply is not None:
            return reply
        run = _ReplayRun(ctx, items, trace)
        state = _ReplayState(screen, time.monotonic() + _budget_seconds(ctx))
        for index, action in (cursor := _StepCursor(items)):
            reply = await _replay_one(run, cursor, (index, action), state)
            if reply is not None:
                return reply

        return _end_of_script(run, state.screen)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.executor.replay failed")
        return {"error": str(exc), "content": None}


def _step_prelude(
    run: _ReplayRun, state: _ReplayState, index: int, action: object
) -> tuple[dict | None, dict | None]:
    """The entry for step *index*, or the reply that stops the script before it.

    Checks the saved route (S10, PROVISIONAL): the live screen must be the one
    this step was saved from, BEFORE the guard and before anything is actuated.
    Then captures the BEFORE set for ``assert new_text`` for every op that can
    change the screen -- ``wait`` included, because the reply a case is waiting
    for arrives during the wait."""
    screen = state.screen
    started = time.monotonic()
    entry = _entry(index, action, _screen_id(screen), started, _screen_hash(screen))
    _stamp_screen_key(run.ctx, entry, screen)
    op = str(getattr(action, "op", "") or "")
    expected_id = _route_expected(run.ctx, index, len(run.items))
    if expected_id and expected_id != _screen_id(screen):
        return entry, _route_mismatch_stop(run, index, entry, screen)
    state.baseline_texts = _before_texts(op, screen, state.baseline_texts)
    return entry, None


async def _replay_one(
    run: _ReplayRun, cursor: _StepCursor, item: tuple, state: _ReplayState
) -> dict | None:
    """One step of the replay; the reply that ends the script, else ``None``."""
    index, action = item
    # NEVER on index 0. A budget that can stop the first action makes no
    # progress at all, and each stop costs one of three escapes, so a short
    # budget would turn every case into `blocked` without ever touching the
    # device.
    if index and time.monotonic() >= state.deadline:
        return _budget_stop(run, index, state.screen)
    entry, reply = _step_prelude(run, state, index, action)
    if reply is not None:
        return reply
    op = str(getattr(action, "op", "") or "")
    # --- terminal ops -------------------------------------------------
    if op == "done":
        return _done_stop(run, action, index, entry, state.screen)
    if op == "ask_tester":
        return _ask_tester_step(run, action, index, entry, state.screen)

    # Saved app notes, BEFORE the target resolves. A wait note polls
    # (bounded); an avoid note refuses. The destructive guard below
    # still runs and still outranks both.
    state.screen, reply, inserted = await _knowledge_before(
        run, cursor, (index, action, entry), state.screen, state.deadline
    )
    if reply is not None or inserted:
        return reply

    # --- target resolution, then the destructive guard ----------------
    step, reply = await _target_step(run, (index, action, entry), op, state.screen)
    if reply is not None:
        return reply

    # --- asserts, waits and device ops --------------------------------
    before = state.screen
    state.screen, reply = await _dispatch_op(
        run, step, state.screen, state.baseline_texts
    )
    _knowledge_post(run, entry, before, state.screen)
    return reply


def _no_done_reason(ended_verified: bool, summary: str, asserts_expected: bool) -> str:
    """The reason a script that never called done() ends with.

    A VERIFIED run must say what it verified. The reason used to open "The
    script finished without a done() action" on BOTH exits, so a tester
    reading a pass was told about an omission in the script rather than about
    the app. The omission is still disclosed -- second sentence -- because it
    is why there is no model-written reason here.
    FOUR branches, because two questions are being answered: was anything
    verified, and -- if not -- is that an omission in THIS unit of work?

    The verified branch is split. ``_verification_summary`` describes THIS
    trace, so when the evidence came from an earlier turn of the run there is
    nothing for it to summarise, and the old fallback ("the asserts in this
    script passed") would make a per-script claim about run-wide evidence --
    inaccurate on exactly the path ``_verified`` opens here.
    """
    if ended_verified and summary:
        return (
            "Verified: "
            + summary
            + ". The script ended without a done() action, so this verdict "
            "rests on the asserts above."
        )
    if ended_verified:
        return (
            "This turn ended without a done() action. It asserted nothing "
            "itself; an earlier turn of this run did, and that is what the "
            "verdict rests on."
        )
    if asserts_expected:
        return (
            "The script finished without a done() action. Nothing in it "
            "asserted anything, so there is no evidence this case passed "
            "-- a screen that moved is activity, not verification."
        )
    return (
        "This turn ended without a done() action and asserted nothing, "
        "which is ordinary for an exploratory turn -- it recorded what "
        "the screen did. No verdict is claimed for it. The run's "
        "verdict comes from the turn that judges the goal."
    )


def _end_of_script(run: _ReplayRun, screen: dict | None) -> dict:
    """A script that never calls done() lands here. It used to hand back an
    empty verdict, which `case_runner` reads as PASS -- so omitting one word
    bypassed the whole verification rule the done() branch enforces. The same
    rule applies on both exits or it is not a rule."""
    ctx, trace = run.ctx, run.trace
    # ``_verified``, not ``_has_verification``: the done() branch asks the
    # run-wide question (:1533) and this exit asked the narrower one, so an
    # explore run whose earlier turn DID assert was still told nothing had been
    # verified whenever a turn omitted done(). That is the refusal
    # ``prior_verified`` exists to remove, surviving on the other exit -- and
    # the scripted lane is unmoved, because that field defaults to False.
    ended_verified = _verified(trace, ctx)
    reason = _no_done_reason(
        ended_verified, _verification_summary(trace), ctx.asserts_expected
    )
    # THE TRACE THIS REASON IS SUMMARISING, actually read. Every branch above
    # is derived from `ended_verified` and `ctx.asserts_expected` alone, so all
    # four claimed the script did something -- "it recorded what the screen
    # did" -- on a script where every action came back `no_change`. Appended
    # rather than folded into the ladder because it is orthogonal to all four:
    # a verified turn and an exploratory one are equally worth telling that
    # nothing moved.
    inert = inert_ops(trace)
    if inert:
        reason = (NOTHING_MOVED_NOTE % ", ".join(inert)) + reason
    return {
        "error": None,
        "content": _result(
            STATUS_DONE,
            trace,
            screen,
            "" if ended_verified else STATUS_UNVERIFIED,
            reason,
            len(run.items) - 1,
        ),
    }
