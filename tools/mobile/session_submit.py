"""Mobile session: submit and explore submit. Cut verbatim from tools/mobile/session.py (P4.4)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from tools.mobile import actions as actions_mod
from tools.mobile import (
    carry_policy,
    case_runner,
    explore_runner,
    lock_reaper,
    media,
    scheduler,
)
from tools.mobile import charter as charter_mod
from tools.mobile.replay.context import SUBMIT_BUDGET_S, Context
from tools.mobile.replay.replay import replay as replay_replay
from tools.mobile.session_audit import MOBILE_MAX_TESTER_INPUTS
from tools.mobile.session_core import (
    LANE_EXPLORE,
    PACKET_RESERVE_S,
    STATE_GATE,
    STATE_REPORT,
    STATE_RUNNING,
)
from tools.mobile.session_explore import (
    _checkpoint_explore_turn,
    _explore_abort_network,
    _explore_finish_network,
    _explore_planned_case,
    _explore_prior_turn_blocked,
    _explore_prior_verified,
    _latest_finding,
    _persist_explore,
    _prior_guard_stop,
    _prior_guard_stop_explore,
    _TurnNotes,
    explore_turn_case_id,
)
from tools.mobile.session_packets import _finish_evidence
from tools.mobile.session_runs import context_for, load_case, resolve

logger = logging.getLogger("tools.mobile.session")


def _combine_queued_actions(queued: object, new_actions: object) -> dict:
    """Actions left queued by a budget stop, folded with a fresh submit.

    Delegates to :func:`tools.mobile.actions.combine_queued_actions` -- FU1
    moved the combine rule there so ``case_runner`` (which cannot import
    ``session``) can use the same rule for the SCRIPTED lane. Behaviour-
    identical; see that function's docstring for the queue-first,
    cap-refuses-by-name design.
    """
    from tools.mobile import actions as actions_mod

    return actions_mod.combine_queued_actions(queued, new_actions)


def _merge_tester_inputs(
    tester_input: str, tester_input_field: str, tester_inputs: dict | None
) -> dict:
    """The legacy single ``(field, value)`` pair plus a multi-field dict, merged
    into ONE dict :func:`submit` puts on ``Context.tester_inputs``.

    ``tester_inputs`` wins on a colliding field name -- it is the newer, more
    specific form, and a caller sending both is choosing to override the
    legacy pair rather than fighting it silently. Each field name is
    truncated to 80 chars, matching the legacy single-field cap. More than
    :data:`MOBILE_MAX_TESTER_INPUTS` fields raises ``ValueError`` naming the
    cap -- :func:`submit` returns it as the error, and runs nothing -- rather
    than dropping fields silently, so an oversized payload cannot grow the audit's ``tester_fields`` list, or the
    executor's per-field lookups, without bound.
    """
    merged: dict = {}
    legacy_field = str(tester_input_field or "").strip()[:80]
    if legacy_field and str(tester_input or ""):
        merged[legacy_field] = str(tester_input)
    if isinstance(tester_inputs, dict):
        for raw_name, value in tester_inputs.items():
            name = str(raw_name or "").strip()[:80]
            if name and str(value or ""):
                merged[name] = str(value)
    if len(merged) > MOBILE_MAX_TESTER_INPUTS:
        raise ValueError(
            "this submit names %d tester fields; one submit takes at most %d "
            "(MOBILE_MAX_TESTER_INPUTS). Nothing was run: answer only the "
            "fields this screen asks for and send the rest on a later submit."
            % (len(merged), MOBILE_MAX_TESTER_INPUTS)
        )
    return merged


SINGLE_ACTION_NOTICE = (
    "This script carried one action. A script may carry up to "
    "{max_actions} -- a tap, the type it triggers and the assert that "
    "checks it usually fit in one script together, and each extra round trip "
    "through this tool is one the tester's own client timer is also paying "
    "for. Batch the next few actions you already know you want when they do "
    "not depend on an unpredictable reply."
)


def _single_action_notice(raw_script: object) -> str:
    """ "" unless *raw_script* parses to exactly one action.

    Never raises and never refuses: read AFTER `case_runner.submit_case`
    already validated and replayed the same script, so a parse failure here
    can only mean the script changed between the two reads, which never
    happens on this call's own object. Audit item 3 -- the schema already
    allowed up to `actions.MAX_MODEL_ACTIONS`; nothing said so where a planner
    would see it before scripting one action per submit. VERIFIED (not
    UNVERIFIED): `actions_mod.parse_script`'s own docstring says it
    "[a]ccepts the packet shape ({\"actions\": [...]}), a bare list, or a
    JSON string of either" -- `tools/mobile/actions.py:636-642` -- so this
    call on the raw string `submit()` already received is exactly one of
    the three documented shapes, not a new assumption.
    """
    try:
        parsed = actions_mod.parse_script(raw_script)
        script = parsed.get("content")
        if script is not None and len(script.actions) == 1:
            return SINGLE_ACTION_NOTICE.format(
                max_actions=actions_mod.MAX_MODEL_ACTIONS
            )
    except Exception:
        logger.debug("mobile.session._single_action_notice failed", exc_info=True)
    return ""


async def submit(
    run_id: str,
    tc_id: str,
    raw_script: object,
    *,
    session_token: str = "",
    tester_input: str = "",
    tester_input_field: str = "",
    tester_inputs: dict | None = None,
    confirm_destructive: bool = False,
    budget: object = None,
    route_expect: object = None,
) -> dict:
    """Replay one answered packet and return the verdict plus the NEXT packet.

    The tester's value(s) are put on ``Context.tester_inputs`` and nowhere
    else: not returned, not checkpointed, not logged and not part of any dict
    this function hands back. ``tester_input``/``tester_input_field`` is the
    legacy single-field pair; ``tester_inputs`` is a dict for a submit that
    answers TWO OR MORE fields at once (a password plus a one-time code, say)
    -- see :func:`_merge_tester_inputs`. The FIELD NAMES travel; the values do
    not.
    """
    try:
        resolved = resolve(run_id, session_token)
        if resolved.get("error"):
            return resolved
        lock_reaper.touch_activity(run_id)
        body = resolved["content"] or {}
        ctx = context_for(body)
        _cap_submit_budget(ctx, budget)
        field = str(tester_input_field or "").strip()[:80]
        try:
            merged_inputs = _merge_tester_inputs(
                tester_input, tester_input_field, tester_inputs
            )
        except ValueError as exc:
            return {"error": str(exc), "content": None}
        _hold_tester_inputs(run_id, ctx, merged_inputs)
        ctx.confirm_destructive = bool(confirm_destructive)
        # S10 saved route (PROVISIONAL): empty unless the wrapper is replaying a route.
        ctx.route_expect = _route_expect_tuple(route_expect)
        if str(body.get("lane")) == LANE_EXPLORE:
            return await _submit_explore_held(run_id, raw_script, ctx, body)
        target = _SuiteTarget(run_id, tc_id, body, field)
        return await _submit_suite(target, raw_script, ctx)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.session.submit failed")
        return {"error": str(exc), "content": None}


def _route_expect_tuple(route_expect: object) -> tuple[str, ...]:
    """The expected route as a tuple of strings; empty for anything else."""
    if isinstance(route_expect, (list, tuple)):
        return tuple(str(item) for item in route_expect)
    return ()


def _cap_submit_budget(ctx: Context, budget: object) -> None:
    """Cap this submit's replay budget so ``PACKET_RESERVE_S`` is left over."""
    if budget is None:
        return
    # Caps THIS submit's replay so PACKET_RESERVE_S is still there for
    # next_packet afterward -- without this, a slow action can spend
    # the whole call budget on the replay and next_packet's busy
    # reply is left with nothing to build a packet from. Never above
    # SUBMIT_BUDGET_S: that constant is unchanged and still the
    # ceiling.
    ctx.budget_s = max(
        1.0,
        min(SUBMIT_BUDGET_S, budget.remaining() - PACKET_RESERVE_S),
    )


def _hold_tester_inputs(run_id: str, ctx: Context, merged: object) -> None:
    """Remember this call's inputs for the run and put every held one on *ctx*."""
    # HELD FOR THE RUN (fix round 3, item 1). On the Air run the tester
    # gave the login fields once and was asked again on five of eight
    # turns, because a value lived on the Context for ONE call. It is now
    # held in process memory by `held_inputs` -- never on disk, and never
    # in this module, which keeps no state in memory -- and every later
    # submit of the same run carries it, so `ask_tester` finds it already
    # supplied. A value given on THIS call wins over a held one. The hold
    # is dropped when the SUBMIT ITSELF reaches the report (both lanes,
    # below); any other way a run ends relies on the sliding TTL.
    from tools.mobile import held_inputs

    held_inputs.remember(run_id, merged)
    carried = held_inputs.recall(run_id)
    if carried or merged:
        ctx.tester_inputs = dict(carried, **dict(merged or {}))


async def _submit_explore_held(
    run_id: str, raw_script: object, ctx: Context, body: dict
) -> dict:
    """Submit on the explore lane; drop the held inputs once it reports."""
    from tools.mobile import held_inputs

    explored = await _submit_explore(run_id, raw_script, ctx, body)
    content = explored.get("content") if isinstance(explored, dict) else None
    if isinstance(content, dict) and content.get("state") == STATE_REPORT:
        held_inputs.forget(run_id)
    return explored


@dataclass(frozen=True)
class _SuiteTarget:
    """Which case of which run a suite-lane submit answers, and its reply context."""

    run_id: str
    tc_id: str
    body: dict
    field: str


async def _submit_suite(target: _SuiteTarget, raw_script: object, ctx: Context) -> dict:
    """Replay one suite-lane answer and build the verdict plus next-step reply."""
    from tools.mobile import held_inputs

    run_id, tc_id, body, field = (
        target.run_id,
        target.tc_id,
        target.body,
        target.field,
    )
    # Scoped to THIS tc_id, not a run-wide scan -- see `_prior_guard_stop`.
    ctx.prior_guard_stop = _prior_guard_stop(run_id, tc_id)
    loaded = load_case(run_id, tc_id)
    if loaded.get("error"):
        return loaded
    result = await case_runner.submit_case(run_id, loaded["content"], ctx, raw_script)
    if result.get("error"):
        return result
    content = dict(result.get("content") or {})
    follow = (scheduler.next_case(run_id) or {}).get("content") or {}
    state = (
        STATE_RUNNING
        if content.get("packet")
        else (
            STATE_REPORT
            if follow.get("finished")
            else STATE_GATE
            if follow.get("gate")
            else STATE_RUNNING
        )
    )
    if state == STATE_REPORT:
        await _finish_evidence(run_id, body)
        held_inputs.forget(run_id)
    return {
        "error": None,
        "content": {
            "state": state,
            "case": content,
            "packet": content.get("packet"),
            "next": follow,
            "field": field,
            "resolved": body,
            "notice": _single_action_notice(raw_script),
        },
    }


def _explore_needs_model(reason: str, resolved: dict) -> dict:
    """A refused explore turn: logged, then handed back to the model to rewrite."""
    case_runner.note_refusal(reason)
    return {
        "error": None,
        "content": {
            "state": STATE_RUNNING,
            "case": {"status": case_runner.NEEDS_MODEL, "reason": reason},
            "packet": None,
            "next": {},
            "field": "",
            "resolved": resolved,
        },
    }


def _prime_explore_turn(run_id: str, ctx: Context, payload: dict) -> None:
    """Expect the apps this turn names; carry the screen only after an inert turn."""
    from tools.mobile import actions as actions_mod

    ctx.expect_apps = tuple(actions_mod.expect_apps_from(payload))
    ctx.carry_screen = carry_policy.allowed(run_id)


def _noted_outcome(run_id: str, replayed: dict) -> dict:
    """The replay's content, after noting whether the turn actuated anything."""
    outcome = replayed.get("content") or {}
    carry_policy.note(run_id, outcome.get("trace"))
    return outcome


def _parse_explore_submission(
    raw: object, resolved: dict
) -> tuple[dict, dict, dict | None]:
    """Decode and parse the model's reply: (payload, parsed script, refusal)."""
    from tools.mobile import actions as actions_mod

    # DECODED, not type-tested. `qa_submit_mobile_step` declares `script: str`,
    # so this used to be `{}` on every real client path: the whole JSON string
    # went to `parse_script`, `Script`'s `extra="forbid"` refused the advertised
    # `finding` key, and nothing replayed -- and when a model retried with a
    # bare actions array, `apply_turn_result` got `{}` and never read the
    # finding. Two failures, one cause, and no test saw either because every
    # test passed a dict into this helper.
    payload = actions_mod.decode_reply(raw)
    # SERVER-SIDE queue (defect F2): actions a prior budget stop left unreached
    # ride the persisted explore state and run FIRST, ahead of whatever the
    # model just submitted -- see _combine_queued_actions.
    queued_in = list((resolved.get("explore") or {}).get("queued_actions") or [])
    if queued_in:
        combined = _combine_queued_actions(
            queued_in, list(payload.get("actions") or [])
        )
        if not combined.get("ok"):
            refusal = _explore_needs_model(str(combined.get("reason") or ""), resolved)
            return payload, {}, refusal
        payload["actions"] = combined["actions"]
    # A folded queue was already held to both caps by the combine, so only a
    # bare submission is held to the model cap here.
    parsed = actions_mod.parse_script(
        payload.get("actions"),
        max_actions=(
            actions_mod.MAX_ACTIONS if queued_in else actions_mod.MAX_MODEL_ACTIONS
        ),
    )
    if parsed.get("error"):
        return payload, parsed, _explore_needs_model(parsed["error"], resolved)
    return payload, parsed, None


def _arm_explore_guards(run_id: str, ctx: Context, resolved: dict) -> None:
    """Set the run-wide evidence and guard policy the executor reads."""
    # Run-wide evidence, read from the checkpoints this run already wrote and
    # passed in EXPLICITLY. See ``_explore_prior_verified``.
    ctx.prior_turn_blocked = _explore_prior_turn_blocked(run_id)
    ctx.prior_verified = _explore_prior_verified(run_id)
    # WHAT HAPPENS AFTER A GUARD HIT, from this run's own charter. The guard
    # itself is unchanged and no value here lets an action through it:
    # ``charter.guard_policy`` maps ``destructive: none`` to REFUSE, and the
    # executor then ENDS the attempt by name instead of pausing for the
    # tester. An explicit input, set beside ``prior_verified`` for the same
    # reason -- the executor knows nothing about runs or charters.
    ctx.guard_refuses = (
        charter_mod.guard_policy(
            ((resolved.get("explore") or {}).get("charter") or {}).get("destructive")
        )
        == charter_mod.REFUSE
    )
    # THIS turn's own id, computed BEFORE the replay. The exact-match
    # lookup alone no longer suffices here: `explore_runner.next_packet`
    # bumps `state["turn"]` on EVERY call, so a confirm submitted after the
    # scheduler already advanced past this turn names a `tc_id` no case was
    # ever checkpointed under. See `_prior_guard_stop_explore`.
    ctx.prior_guard_stop = _prior_guard_stop_explore(
        run_id, explore_turn_case_id(resolved.get("explore"))
    )


def _turn_verdict(outcome: dict) -> str:
    """The verdict of a turn whose ``done()`` judged the goal, else ""."""
    # Only a turn whose ``done()`` judged the goal writes a verdict; every
    # other turn keeps "" plus its status, because an exploratory turn has no
    # expected result to be judged against. It lands in the ordinary
    # ``verdict`` field, exactly as the suite lane writes it, so
    # ``run_store.DONE_VERDICTS`` and every reader of it work unchanged.
    #
    # The condition is STATED, not inferred from the producer:
    # ``executor.replay``'s no-done exit returns ``unverified`` too, so reading
    # the field alone recorded a turn that merely acted as one that had reached
    # a verdict.
    ended = any(
        str((entry.get("action") or {}).get("op") or "") == "done"
        for entry in list(outcome.get("trace") or [])
        if isinstance(entry, dict)
    )
    return str(outcome.get("verdict") or "") if ended else ""


def _explore_turn_case(checkpoint: dict, outcome: dict, verdict: str) -> dict:
    """The ``case`` block of the chat reply for one replayed turn."""
    return {
        "tc_id": checkpoint["tc_id"],
        "title": checkpoint["title"],
        "status": str(outcome.get("status") or ""),
        "verdict": verdict,
        "reason": str(outcome.get("reason") or ""),
        "trace": list(outcome.get("trace") or []),
    }


def _explore_turn_reply(
    case: dict, notice: str, resolved: dict, *, stopped: bool
) -> dict:
    """The chat reply for one folded exploratory turn."""
    return {
        "error": None,
        "content": {
            "state": STATE_REPORT if stopped else STATE_RUNNING,
            "case": case,
            "packet": None,
            "next": {},
            "notice": notice,
            "field": "",
            "resolved": resolved,
        },
    }


async def _submit_explore(
    run_id: str, raw: object, ctx: Context, resolved: dict
) -> dict:
    """One exploratory turn: replay the actions, then fold the reply."""
    payload, parsed, refusal = _parse_explore_submission(raw, resolved)
    if refusal is not None:
        return refusal
    # HAND-OVER TO THE TARGET APP (fix round 3, item 1). A multi-app goal
    # (App Tester -> the app under test) moves into a second app, and every guard that
    # asks "is this the app under test" reads `ctx.package` -- which stayed
    # the run's FIRST app for the whole Air run, so five turns on the right
    # screen were refused as SCREEN_NOT_THE_APP. `explore_runner.handover`
    # accepts the model's `app` only when it is the package the SERVER saw in
    # front when it built this turn's packet, and never a system surface.
    # Applied BEFORE the replay and carried on the explore state that
    # `apply_turn_result` persists, so `context_for` reads it on every later
    # call. `clear_app_data` keeps `ctx.home_package`.
    handed = explore_runner.handover(resolved.get("explore") or {}, payload)
    if handed:
        resolved = dict(resolved)
        resolved["explore"] = dict(resolved.get("explore") or {}, app=handed)
        ctx.package = handed
        ctx.activity = ""
    _arm_explore_guards(run_id, ctx, resolved)
    replay = await _replay_explore_turn(run_id, ctx, resolved, payload, parsed)
    if isinstance(replay, dict):
        return replay
    return await _commit_explore_turn(run_id, ctx, resolved, payload, replay)


@dataclass(frozen=True)
class _ReplayedTurn:
    """What a replayed exploratory turn hands to its commit step."""

    outcome: dict
    clip: object
    clip_tc_id: str


async def _replay_explore_turn(
    run_id: str, ctx: Context, resolved: dict, payload: dict, parsed: dict
) -> _ReplayedTurn | dict:
    """Start the clip and replay the actions; a replay error is returned as is."""
    # THE RECORDER, on the lane that shipped without one. `case_runner` has
    # started a clip around ITS replay since v1.87.0; this is the other replay
    # site and it had none, so every exploratory run -- which is the lane the
    # testers actually use -- produced a report with no video while the feature
    # read as shipped. Measured on run mrun-20260910-180207-2ee014: five cases,
    # zero media records, nothing failed. One question, two sites, one wired.
    #
    # The id comes from `explore_turn_case_id`, the same producer the checkpoint
    # below writes the case under.
    clip_tc_id = explore_turn_case_id(resolved.get("explore"))
    clip = (
        await media.start_clip(run_id, clip_tc_id, parsed["content"], ctx.serial)
    ).get("content")
    _prime_explore_turn(run_id, ctx, payload)
    replayed = await replay_replay(parsed["content"], ctx)
    if replayed.get("error"):
        # The turn is over and no checkpoint will be written for it, so the
        # capture this turn started has no later reader. Stop it here or the
        # console records the tester's device until something displaces it.
        # The RECORDER is the same shape of leak, one artifact over: no
        # checkpoint means no case to file a record onto, so the clip is
        # stopped and deleted rather than stored -- `case_runner` calls
        # `abandon` on its own error path for exactly this reason.
        await media.abandon(ctx.serial, clip)
        _persist_explore(
            run_id,
            await _explore_abort_network(run_id, resolved.get("explore") or {}, ctx),
        )
        return replayed
    return _ReplayedTurn(_noted_outcome(run_id, replayed), clip, clip_tc_id)


async def _commit_explore_turn(
    run_id: str,
    ctx: Context,
    resolved: dict,
    payload: dict,
    replay: _ReplayedTurn,
) -> dict:
    """Fold the replayed turn into the explore state, checkpoint it and reply."""
    outcome, clip, clip_tc_id = replay.outcome, replay.clip, replay.clip_tc_id
    folded = explore_runner.apply_turn_result(resolved.get("explore") or {}, payload)
    if folded.get("error"):
        return folded
    turn = folded.get("content") or {}
    # Carried on `turn["state"]`, not `turn` itself: `committed_state` below is
    # built from `turn.get("state")`, and that is the ONLY part of `turn`
    # `_persist_explore` writes back into `resolved["explore"]` -- a queue
    # attached anywhere else on `turn` would never reach the next submit.
    if isinstance(turn.get("state"), dict):
        turn["state"]["queued_actions"] = outcome.get("queued_actions") or []
    # The turn just replayed, checkpointed as a case. ``resolved["explore"]``
    # carries the turn NUMBER this submit answered (``next_turn`` incremented
    # and persisted it before the packet went out), so the id is the turn's own
    # and is the same on a resubmit.
    before = resolved.get("explore") or {}
    finding = _latest_finding(turn.get("state"))
    # ONE producer for this turn's verdict, read by BOTH consumers below --
    # the DISK record and the CHAT reply.
    verdict = _turn_verdict(outcome)
    # The turn HAS been replayed, so its capture is complete. Stopped before the
    # checkpoint, because the checkpoint is what carries the record to the report.
    net = await _explore_finish_network(run_id, before, ctx)
    checkpoint = _checkpoint_explore_turn(
        run_id, before, outcome, _TurnNotes(finding, verdict, net)
    )
    # AFTER the checkpoint, and that ordering is the contract, not a style
    # choice. `media.remember` APPENDS onto the case record; in this lane the
    # checkpoint above is what CREATES that record, so calling this first would
    # find no case, log the loss and drop the clip on the floor. The case lane
    # is the other way round only because `start_case` wrote its record before
    # the submit began. Nothing rewrites the case after this point, so the
    # records survive without a carry-forward of their own.
    await media.finish_step(
        run_id,
        clip_tc_id,
        ctx.serial,
        clip,
        str((outcome.get("screen") or {}).get("screen_id") or ""),
    )
    return await _settle_explore_turn(run_id, resolved, turn, outcome, checkpoint)


async def _settle_explore_turn(
    run_id: str, resolved: dict, turn: dict, outcome: dict, checkpoint: dict
) -> dict:
    """Persist the committed explore state, close the run if over, and reply."""
    before = resolved.get("explore") or {}
    finding = _latest_finding(turn.get("state"))
    verdict = _turn_verdict(outcome)
    committed_state, refused = _committed_explore_state(turn, outcome)
    _persist_explore(
        run_id,
        committed_state,
        planned=_explore_planned_case(checkpoint["tc_id"], before, finding),
    )
    # ONE derivation of "has this run stopped", read by BOTH consumers below.
    # The two used to be two copies of the same condition, and a refusal must
    # move both or the chat reply invites a next turn the run will not give.
    stopped = refused or str(turn.get("status") or "") != explore_runner.RUNNING
    if stopped:
        await _finish_evidence(run_id, resolved)
    notice = (
        explore_runner.GUARD_REFUSED_NOTICE
        if refused
        else str(turn.get("notice") or "")
    )
    return _explore_turn_reply(
        _explore_turn_case(checkpoint, outcome, verdict),
        notice,
        resolved,
        stopped=stopped,
    )


def _committed_explore_state(turn: dict, outcome: dict) -> tuple[dict, bool]:
    """The explore state to persist for a replayed turn, and whether it refused."""
    # The replay HAPPENED, so the number this turn was handed is now earned.
    # This is the only writer that ADVANCES ``committed_turn`` to a number the
    # ladder has not already earned, and it
    # sits below every path that ends without a replay: the parse refusal above
    # returns before ``executor.replay``, a replay error returns before this
    # line, and a packet nobody ever answers never reaches this module at all.
    # Each of those leaves the ledger where it was, so
    # ``explore_runner.next_turn`` re-allocates the SAME number next time and the
    # report grows neither a gap nor a duplicate card.
    #
    # No coercion guard here, and no ``# pragma``. ``apply_turn_result`` above
    # calls ``explore_runner.stop_reason``, which coerces this same ``turn``, so
    # on every path where ``state["stop"]`` is empty a junk turn number has
    # already been answered with a refusal before this line -- a guard here would
    # be a branch no test can enter, an ungraded clause wearing a note.
    #
    # Where ``stop`` is NOT empty, ``stop_reason`` returns before its coercion
    # and this line does raise; ``submit``'s own try/except contains it and the
    # caller gets an error dict, never an exception. That corner is why the guard
    # is deleted rather than corrected: it used to answer with
    # ``committed_turn = 0``, which restarts numbering at TC-001 over cards the
    # run already wrote.
    committed_state = dict(turn.get("state") or {})
    committed_state["committed_turn"] = int(committed_state.get("turn") or 0)
    # This turn's capture is finished and its record now lives on the case
    # record. Leaving it on the state would have the NEXT turn's begin treat it
    # as a capture still running and abort a file that is already gone.
    committed_state.pop(
        "network", None
    )  # THE REFUSE, RECORDED AS A STOP. The executor marks a charter-driven
    # refusal explicitly (``guard_refused``), and a refused run is OVER -- so
    # the state says so and every reader under ``stop_reason`` sees it.
    # Without this the reply would carry the refusal text and still report a
    # running run: a success-shaped message behind a refusal, which is exactly
    # what this lane's rule forbids.
    refused = bool(outcome.get("guard_refused"))
    if refused:
        committed_state["stop"] = explore_runner.STOP_GUARD_REFUSED
    return committed_state, refused
