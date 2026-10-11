"""Replay engine: per-step stop handling, knowledge stamps and target resolution. Cut verbatim from tools/mobile/executor.py (P4.5)."""

from __future__ import annotations

import asyncio
import logging
import time

from tools.mobile import actions as actions_mod
from tools.mobile import (
    fill_label,
    knowledge_hooks,
)
from tools.mobile.replay.context import (
    STATUS_DONE,
    STATUS_ERROR,
    STATUS_NEEDS_MODEL,
    STATUS_NEEDS_TESTER,
    STATUS_UNVERIFIED,
    Context,
    _append,
    _ReplayRun,
    _result,
    _Step,
    _StepCursor,
)
from tools.mobile.replay.elements import (
    _clamp_source,
    _screen_elements,
    _touch_point,
    actuated_element,
    element_label,
    missing_element_detail,
    resolve_tap_text,
)
from tools.mobile.replay.guard import (
    GUARD_DETAIL_REFUSED,
    NON_ACTUATING_OPS,
    _asserts_new_element,
    _consume_confirm,
    _guard_node,
    _with_confirm_hint,
    destructive_hit,
    guard_detail,
    screen_hit,
)
from tools.mobile.replay.screen import _stamp_after, _verified
from tools.mobile.replay.verdicts import (
    MAX_VISUAL_DETAIL_CHARS,
    ROUTE_MISMATCH_DETAIL,
    _actuated,
    _evaluate_assert,
    _serialize_queued,
    budget_stop_reason,
)

logger = logging.getLogger("tools.mobile.executor")


def _budget_stop(run: _ReplayRun, index: int, screen: dict | None) -> dict:
    """The reply for a replay that ran out of wall-clock before action *index*."""
    # SERVER-SIDE queue (defect F2): the unreached actions ride along on THIS
    # reply so the next submit can run them first instead of the model
    # retyping them.
    # A route replay queues NOTHING: the remainder has not been checked
    # against its saved screens and must not run blind.
    is_route = bool(getattr(run.ctx, "route_expect", None))
    queued = [] if is_route else _serialize_queued(run.items[index:])
    return {
        "error": None,
        "content": _result(
            STATUS_NEEDS_MODEL,
            run.trace,
            screen,
            "",
            budget_stop_reason(index, len(run.items), len(queued), route=is_route),
            index,
            # NOT an escape. The budget can stop a script the validator called
            # legal -- the per-wait re-dump costs wall clock that the wait cap
            # does not count -- and charging one of three escapes for obeying
            # our own bound is how a correct case becomes `blocked`.
            budget_stop=True,
            queued_actions=queued,
        ),
    }


def _route_mismatch_stop(
    run: _ReplayRun, index: int, entry: dict, screen: dict | None
) -> dict:
    """The reply for a saved-route step whose live screen is not the one it was
    saved from. Nothing has been actuated for this step."""
    entry["outcome"] = "route_mismatch"
    entry["detail"] = ROUTE_MISMATCH_DETAIL
    _stamp_after(entry, screen)
    _append(run.trace, entry)
    return {
        "error": None,
        "content": _result(
            STATUS_NEEDS_MODEL,
            run.trace,
            screen,
            "",
            ROUTE_MISMATCH_DETAIL,
            index,
            # Uncharged by case_runner while nothing has actuated (the free
            # stop for a stale selector); a hop AFTER an actuated one is charged.
            selector_stale=True,
            actuated=_actuated(run.trace),
        ),
    }


def _done_stop(
    run: _ReplayRun, action: object, index: int, entry: dict, screen: dict | None
) -> dict:
    """The reply for a ``done`` step. A ``pass`` nothing verified is downgraded."""
    verdict = str(getattr(action, "verdict", ""))
    reason = str(getattr(action, "reason", ""))[:400]
    if verdict == "pass" and not _verified(run.trace, run.ctx):
        verdict = STATUS_UNVERIFIED
        reason = (
            "verdict=pass was not accepted: nothing in this run "
            "has recorded an assert_pass, so nothing was verified. "
            "Add an `assert` for what the case is supposed to "
            "show. " + reason
        ).strip()
    elif (
        verdict == "pass"
        and run.ctx.prior_turn_blocked
        and not _asserts_new_element(run.trace)
    ):
        verdict = STATUS_UNVERIFIED
        reason = (
            "verdict=pass was not accepted: the previous turn failed "
            "to type or changed nothing on screen, and this turn "
            "asserts no new element. Add an `assert` of kind "
            "`element` or `new_text` for what the step produced. " + reason
        ).strip()
    entry["outcome"] = "done"
    entry["detail"] = reason
    _stamp_after(entry, screen)
    _append(run.trace, entry)
    return {
        "error": None,
        "content": _result(
            STATUS_DONE,
            run.trace,
            screen,
            verdict,
            reason,
            index,
        ),
    }


def _ask_tester_step(
    run: _ReplayRun, action: object, index: int, entry: dict, screen: dict | None
) -> dict | None:
    """An ``ask_tester`` step: the reply that asks, or ``None`` when the tester
    already supplied the field and the replay goes on."""
    field = str(getattr(action, "field", ""))
    supplied = (run.ctx.tester_inputs or {}).get(field)
    if supplied is None:
        entry["outcome"] = "needs_tester"
        entry["detail"] = str(getattr(action, "prompt", ""))[:300]
        _stamp_after(entry, screen)
        _append(run.trace, entry)
        return {
            "error": None,
            "content": _result(
                STATUS_NEEDS_TESTER,
                run.trace,
                screen,
                "",
                entry["detail"],
                index,
                field=field,
            ),
        }
    entry["outcome"] = "supplied"
    entry["detail"] = "the tester supplied " + field
    _stamp_after(entry, screen)
    _append(run.trace, entry)
    return None


def _stamp_screen_key(ctx: Context, entry: dict, screen: object) -> None:
    """In-memory ``screen_key`` + ``activity`` for the knowledge learner."""
    try:
        from tools.mobile import screen_key

        activity = str(getattr(ctx, "activity", "") or "")
        entry["screen_key"] = screen_key.screen_key(
            screen, str(getattr(ctx, "package", "") or ""), activity
        ).key
        entry["activity"] = activity
    except Exception:
        logger.exception("mobile app knowledge: screen key failed")


def _stamp_after_key(ctx: Context, entry: dict, screen: object) -> None:
    try:
        from tools.mobile import screen_key

        entry["after_screen_key"] = screen_key.screen_key(
            screen,
            str(getattr(ctx, "package", "") or ""),
            str(getattr(ctx, "activity", "") or ""),
        ).key
    except Exception:
        logger.exception("mobile app knowledge: after screen key failed")


def _mark_refused(ctx: Context, fired: object) -> None:
    """An ``insert_steps`` the cursor refused (budget spent, route-locked): the
    plug's ``*_fired`` event for that item is marked ``refused`` so it is never
    counted as having run."""
    item = (fired or {}).get("item") if isinstance(fired, dict) else None
    for ev in reversed(getattr(ctx, "kbuf", None) or []):
        if (
            isinstance(ev, dict)
            and ev.get("item") == item
            and str(ev.get("kind", "")).endswith("_fired")
        ):
            ev["refused"] = True
            return


async def _knowledge_pre(
    run: _ReplayRun,
    cursor: _StepCursor,
    step: tuple[int, object, dict],
    screen: object,
    deadline: float,
) -> tuple[dict | None, bool]:
    """``pre_step`` then the adaptive wait, once each: ``(reply, inserted)``.
    ``reply`` ends the replay (a refuse or stop directive); ``inserted`` means
    steps were spliced in and the loop must re-enter at the same index."""
    ctx, (index, action, entry) = run.ctx, step
    ctx.script_ahead = list(cursor.items[index:])
    directive = await knowledge_hooks.pre_step(ctx, entry, action, screen)
    if directive.kind in ("refuse", "stop"):
        why = directive.reason or "stopped by app knowledge"
        return _knowledge_stop(run, index, entry, screen, why), False
    if directive.kind == "insert_steps":
        consume = int((directive.fired or {}).get("consume") or 0)
        inserted = cursor.insert(
            index, directive.steps, bool(ctx.route_expect), consume
        )
        if not inserted:
            _mark_refused(ctx, directive.fired)
        return None, inserted
    ms = knowledge_hooks.step_wait(ctx, action, screen)
    left = int((deadline - time.monotonic()) * 1000)
    ms = min(ms, int(actions_mod.MAX_WAIT_MS), left)
    if ms > 0:
        await asyncio.sleep(ms / 1000.0)
    return None, False


def _knowledge_post(
    run: _ReplayRun, entry: dict, before: object, after: object
) -> None:
    """After a step: stamp ``after_screen_key``, then ``post_step`` (in memory)."""
    _stamp_after_key(run.ctx, entry, after)
    knowledge_hooks.post_step(run.ctx, entry, before, after)


async def _knowledge_target(
    run: _ReplayRun, entry: dict, action: object, screen: object, found: tuple
) -> tuple[object, object]:
    """``resolve_target`` once on ``found = (element, reply)``. A
    ``replace_target`` directive sets the element and clears the reply (the plug
    found what the default could not); a miss the default already committed to
    the trace is taken back, so the step is recorded once, by its real outcome."""
    element, reply = found
    got = await knowledge_hooks.resolve_target(run.ctx, action, screen, element)
    if got.kind != "replace_target" or got.target is None:
        return element, reply
    if reply is not None:
        if run.trace and run.trace[-1] is entry:
            run.trace.pop()
        for key in ("outcome", "detail", "after_screen_id", "after_screen_hash"):
            entry.pop(key, None)
        # ``_append`` popped ``_started``; restore it so the final ``ms`` covers
        # the whole step, not just the miss.
        if "ms" in entry:
            entry["_started"] = time.monotonic() - int(entry.pop("ms")) / 1000.0
    return got.target, None


def _knowledge_stop(
    run: _ReplayRun, index: int, entry: dict, screen: dict | None, note_stop: str
) -> dict:
    """The reply for a step a saved ``avoid`` note refuses."""
    entry["outcome"] = "knowledge_avoid"
    entry["detail"] = note_stop
    _stamp_after(entry, screen)
    _append(run.trace, entry)
    return {
        "error": None,
        "content": _result(STATUS_NEEDS_MODEL, run.trace, screen, "", note_stop, index),
    }


def _assert_step(
    run: _ReplayRun,
    action: object,
    entry: dict,
    screen: dict | None,
    baseline_texts: set | None,
) -> dict | None:
    """An ``assert`` step: ``None`` when it passed and the replay goes on, else
    the reply that stops it (a failed assert, or a visual check)."""
    index = entry["index"]
    if str(getattr(action, "kind", "")) == "visual":
        # Nothing here can judge a picture. Stop, and let the next
        # reply carry the screenshot of THIS screen to the model;
        # `Script` already refused a visual that is not last.
        entry["outcome"] = "visual_check"
        entry["detail"] = ("Visual check: " + str(getattr(action, "note", "")))[
            :MAX_VISUAL_DETAIL_CHARS
        ]
        _stamp_after(entry, screen)
        _append(run.trace, entry)
        return {
            "error": None,
            "content": _result(
                STATUS_NEEDS_MODEL,
                run.trace,
                screen,
                "",
                entry["detail"],
                index,
                visual_check=True,
            ),
        }
    ok, detail = _evaluate_assert(action, screen, run.trace, baseline_texts)
    entry["outcome"] = "assert_pass" if ok else "assert_fail"
    entry["detail"] = detail
    _stamp_after(entry, screen)
    _append(run.trace, entry)
    if ok:
        return None
    return {
        "error": None,
        "content": _result(STATUS_NEEDS_MODEL, run.trace, screen, "", detail, index),
    }


def _tap_text_element(
    run: _ReplayRun, action: object, entry: dict, screen: dict | None
) -> tuple[object, dict | None]:
    """A ``tap_text`` step's element, or ``None`` and the reply that stops on a
    text that names no control or several."""
    picked = resolve_tap_text(getattr(action, "text", ""), screen)
    element = picked.get("element")
    if element is not None:
        return element, None
    wanted = str(getattr(action, "text", ""))[:80]
    entry["outcome"] = (
        actions_mod.TAP_TEXT_AMBIGUOUS if picked.get("ambiguous") else "missing_element"
    )
    entry["detail"] = (
        "`"
        + wanted
        + "` is on "
        + str(picked.get("candidates"))
        + " different controls on this screen, so nothing was "
        "tapped. Use `tap` with a narrower target to say which "
        "one you mean."
        if picked.get("ambiguous")
        else "Nothing on this screen carries the text `" + wanted + "`."
    )
    _stamp_after(entry, screen)
    _append(run.trace, entry)
    return element, {
        "error": None,
        "content": _result(
            STATUS_NEEDS_MODEL,
            run.trace,
            screen,
            "",
            entry["detail"],
            entry["index"],
        ),
    }


def _fill_element(
    run: _ReplayRun, action: object, entry: dict, screen: dict | None
) -> tuple[object, dict | None]:
    """A ``fill`` step's field, or ``None`` and the reply that stops on a label
    that names no field or several."""
    picked = fill_label.resolve_fill(
        getattr(action, "label", ""), _screen_elements(screen)
    )
    element = picked.get("element")
    if element is not None:
        return element, None
    entry["outcome"] = "missing_element"
    entry["detail"] = fill_label.refusal_detail(getattr(action, "label", ""), picked)
    _stamp_after(entry, screen)
    _append(run.trace, entry)
    return element, {
        "error": None,
        "content": _result(
            STATUS_NEEDS_MODEL,
            run.trace,
            screen,
            "",
            entry["detail"],
            entry["index"],
        ),
    }


def _selector_stale(resolution: dict) -> bool:
    """Read by ``case_runner``: a stop caused by OUR OWN selector going stale,
    before anything touched the device, is not the tester's case failing to
    make progress -- see MAX_FREE_STOPS there.

    Intersected with ``actions.OURS`` rather than taken from the bare ``stale``
    flag. Measured: ``{"rid": <gone>, "text": <matches>}`` -- no `id` anywhere
    -- reported stale and was UNCHARGED, while OURS' own docstring said a rid
    or text naming something absent is charged like any other boomerang. A
    conflict is charged too: two selectors the MODEL chose disagreeing is a
    stale plan, not our bookkeeping.
    """
    return any(
        name in actions_mod.OURS for name in (resolution.get("stale_selectors") or ())
    )


def _target_element(
    run: _ReplayRun, op: str, target: object, entry: dict, screen: dict | None
) -> tuple[object, bool, dict | None]:
    """The element a step's ``target`` names, whether a ``scroll`` missed it,
    and the reply that stops on a missing element (``None`` to go on)."""
    resolved = actions_mod.resolve_target(target, screen)
    element = ((resolved or {}).get("content") or {}).get("element")
    # ``assert`` is exempt for the same reason ``scroll`` is, and it
    # matters more: a missing element IS the answer an
    # ``assert kind="element"`` was asked for. Letting the generic
    # boomerang intercept it meant that assert could never FAIL --
    # the one verdict it exists to produce. It returned
    # ``needs_model`` instead, so a tester who required an element
    # that was absent got a re-plan, three spent escapes and a
    # ``blocked`` case rather than the failure they asked for.
    # ``_evaluate_assert`` decides presence itself.
    # The DEGRADATION below is deliberate and stays; what changes
    # is that it is now visible. Recorded before the branch so the
    # flag cannot drift from the condition that produces it.
    scroll_missed = element is None and op == "scroll"
    if element is None and op not in ("scroll", "assert"):
        resolution = (resolved or {}).get("content") or {}
        entry["outcome"] = "missing_element"
        entry["detail"] = missing_element_detail(resolution)
        _stamp_after(entry, screen)
        _append(run.trace, entry)
        return (
            element,
            scroll_missed,
            {
                "error": None,
                "content": _result(
                    STATUS_NEEDS_MODEL,
                    run.trace,
                    screen,
                    "",
                    entry["detail"],
                    entry["index"],
                    selector_stale=_selector_stale(resolution),
                    actuated=_actuated(run.trace),
                ),
            },
        )
    return element, scroll_missed, None


def _resolve_element(
    run: _ReplayRun, op: str, action: object, entry: dict, screen: dict | None
) -> tuple[object, bool, dict | None]:
    """The element a step acts on, whether a ``scroll`` missed its target, and
    the reply that stops the replay on a miss (``None`` to go on)."""
    target = getattr(action, "target", None)
    element = None
    # Per ACTION, not per replay: a scroll whose target missed must not
    # colour the note of a later scroll that resolved.
    scroll_missed = False
    if op == "tap_text":
        # Resolved HERE, into the same `element` every other op uses, so
        # the destructive guard below judges `actuated_element` for this
        # op exactly as it does for a `tap`. An op that resolved its own
        # element AFTER the guard would be a bypass, which is the defect
        # class `NON_ACTUATING_OPS` exists to make explicit.
        element, reply = _tap_text_element(run, action, entry, screen)
        if reply is not None:
            return element, scroll_missed, reply
    if op == "fill":
        # Resolved HERE, for the reason `tap_text` is: the destructive
        # guard below must judge the field a fill will touch. The
        # refusal names the label and, when ambiguous, bounded
        # candidates -- never a field's contents.
        element, reply = _fill_element(run, action, entry, screen)
        if reply is not None:
            return element, scroll_missed, reply
    if target is not None:
        return _target_element(run, op, target, entry, screen)
    return element, scroll_missed, None


def _guard_label(
    op: str, action: object, element: object, screen: dict | None
) -> tuple[str, object]:
    """The words the destructive guard judges, and the node it judges: the
    node the action ACTUATES where the touch resolved one, else the one it
    NAMES. Builds the label only; the decision stays in ``_replay_steps``."""
    actuated = None
    labels = [
        actions_mod.action_text(action),
        element_label(element, screen),
    ]
    if element is not None:
        actuated = actuated_element(
            screen,
            _touch_point(op, action, element, _clamp_source(screen)),
        )
        # The actuated node is judged on its OWN strings -- no
        # `screen`, so no containment. It is an ANCESTOR by
        # construction, and an ancestor's contained text is every
        # word inside it: with containment, tapping "Notifications"
        # inside a clickable settings list that also held "Delete
        # account" was refused (an executing review measured it).
        # The named element keeps its containment rule; the residual
        # is a wrapper with no strings of its own whose destructive
        # word lives only in a SIBLING of the child that was named.
        # Appended only when it IS a different node. When the named
        # element is itself the smallest clickable under the finger
        # (the common case) appending it again would join its label
        # to itself, and `destructive_hit` matches multi-word terms
        # as a token RUN: "Money ... send" followed by "Money ..."
        # manufactures "send money" at the seam -- a false stop, and
        # a stop is terminal for a scripted case.
        if actuated is not None and actuated is not element:
            labels.append(element_label(actuated))
    # The node the action ACTUATES where the touch resolved one,
    # else the node it NAMES. The same derivation the actuated-node
    # guard already uses -- not a second one.
    judged_node = actuated if actuated is not None else element
    label = " ".join(labels).strip()
    return label, judged_node


def _record_guard_stop(
    run: _ReplayRun, op: str, hit: str, entry: dict, screen: object
) -> bool:
    """Write the trace entry of a guard stop and say whether the run refuses.

    WHAT the guard stopped is decided in ``_guard_stop`` and is not touched
    here. This decides only what happens AFTER the hit, which is the one thing
    a charter's ``destructive`` field is allowed to decide. The reply that
    carries the term stays in ``_guard_stop``, the reviewed sink.
    """
    entry["outcome"] = "guard_stop"
    refuses = bool(getattr(run.ctx, "guard_refuses", False))
    mismatched = bool(entry.get("guard_confirm_mismatch"))
    # ONE call, not a chain. The chain that was here handled two
    # sentinels and gave every future one the control wording --
    # the same half-wiring at the other consumer is what round 3
    # fixed, and this site still had it.
    entry["detail"] = (
        GUARD_DETAIL_REFUSED
        if refuses
        else _with_confirm_hint(
            guard_detail(hit, screen, run.ctx.package),
            op,
            hit,
            mismatched,
        )
    )
    _stamp_after(entry, screen)
    _append(run.trace, entry)
    return refuses


# --- _guard_stop: why the destructive guard is shaped this way
#
# DENY BY DEFAULT, and judge the node the action ACTUATES, not the node it
# NAMES. Both halves were learned by shipping the failure.
#
# The op half: this was an allow-list of ops believed to tap, and it was
# wrong twice -- 'clear' taps to focus the field, and a TARGETED 'scroll'
# swipes on the resolved element. So every op is guarded unless it is
# declared inert in NON_ACTUATING_OPS.
#
# The node half: the guard judged the RESOLVED element, and Android does not
# deliver a touch to the resolved element. A tap on a non-clickable child
# goes to the nearest CLICKABLE ancestor, so `tap {"text": "OK"}` on the
# child of a `btn_delete_account` wrapper reached the device while `tap
# {"rid": ...}` on the wrapper itself was stopped; and a targeted swipe
# re-centred only its X, so `scroll left` naming a benign row swiped across
# "Slide to confirm payment" at the table's Y. Measured through
# adb._run_argv, both. So the guard ALSO judges `actuated_element` at the
# very point `_perform` will touch, and a targeted swipe is confined to the
# element it names (see `_swipe_points`).
#
# The property this buys: acting on X through a child gets the SAME verdict
# as acting on X directly, so no new false-positive class -- a wrapper that
# stops a tap on its child already stopped a tap on itself.
#
# A target-less pan names nothing and is judged on nothing. THE RESIDUAL,
# stated: the finger starts at the table point, and a slide-to-confirm
# control lying there would be actuated. Widening to that point would stop
# every pan over a transactions list whose row reads "Transfer to ...", and
# a guard stop is terminal for a scripted case, so the pan stays unjudged and
# the decision is pinned in both directions by tests/mobile.
#
# The `press` branch: the key fires the FORM's IME action, and no dump names
# the control that action reaches (uiautomator emits no imeOptions). The
# actuated node is unknown BY CONSTRUCTION, so the scope is every element the
# packet carries -- and a packet that does not carry the whole screen cannot
# clear it. Measured before this line existed: `tap {"text": "Confirm
# payment"}` was refused while `press enter {"rid": amount}` on the same form
# sent KEYCODE_ENTER.
#
# The judged-node branch runs LAST, and only with a node in hand: a path that
# has a term today keeps it, and `judged=None` can never reach the fidelity
# branch, so nothing else about this guard moves.
#
# The refused reply: EXPLICIT KEYWORDS, never a dict splat. The sentinel
# ratchet forbids ``**kwargs`` into this sink because ``guard_term``'s
# provenance must be traceable by reading the call, and it is right: a splat
# hides which keys reach the payload. ``guard_refused`` is an EXPLICIT marker
# rather than a comparison against the detail PROSE -- a reader that matches
# on wording breaks the moment the wording is improved, and the wording is the
# part the tester actually reads.
def _guard_stop(
    run: _ReplayRun, step: _Step, screen: object, index: int
) -> dict | None:
    """The reply that ends the replay at the guard, else ``None`` (why: above)."""
    ctx, trace, op, entry = run.ctx, run.trace, step.op, step.entry
    if not ctx.guard_destructive or op in NON_ACTUATING_OPS:
        return None
    label, judged_node = _guard_label(op, step.action, step.element, screen)
    hit = destructive_hit(label)
    if not hit and op == "press":  # IME action: see the `press` branch above
        hit = screen_hit(screen, ctx.package)
    if not hit and judged_node is not None:  # LAST: see the judged-node branch
        hit = screen_hit(screen, "", judged=judged_node)
    # A flag, not `hit = ""`: the sentinel ratchet requires
    # `hit` be bound ONLY by destructive_hit/screen_hit.
    confirmed = bool(hit) and _consume_confirm(ctx, hit, screen, entry, (op, label))
    if not hit or confirmed:
        return None
    if _record_guard_stop(run, op, hit, entry, screen):
        # EXPLICIT KEYWORDS, never a dict splat: see the refused reply above.
        return {
            "error": None,
            "content": _result(
                STATUS_ERROR,
                trace,
                screen,
                "",
                entry["detail"],
                index,
                guard_term=hit,
                guard_refused=True,
            ),
        }
    return {
        "error": None,
        "content": _result(
            STATUS_NEEDS_TESTER,
            trace,
            screen,
            "",
            entry["detail"],
            index,
            guard_term=hit,
            guard_op=op,
            guard_node=_guard_node(label),
        ),
    }
