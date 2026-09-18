"""The exploratory lane: goal, a turn ladder, a deadline, and one extension.

Four independent stops, because an exploratory session with only one is a
session that runs until somebody notices: ``max_turns``, a wall-clock deadline,
the model declaring ``goal_reached``, and the destructive guard inside the
executor. Exactly ONE extension may be requested, and only with a stated reason
-- "the model asked again" is not a reason, and an unbounded extension is the
same defect as no deadline.

Every clock reading is injected (``now=``). Asserting a 20-minute deadline by
sleeping would be slower than the thing measured and would sit below its own
timing noise floor; the tests drive stated timestamps and assert the STATE
TRANSITION instead.
"""

from __future__ import annotations

import logging
import time

from tools.mobile import adb, executor, run_store
from tools.mobile import charter as charter_mod
from tools.mobile.providers import composite

logger = logging.getLogger(__name__)

MAX_TURNS = 30
DEADLINE_S = 20 * 60
MAX_EXTENSIONS = 1
EXTENSION_TURNS = 15
EXTENSION_S = 10 * 60
MAX_GOAL_CHARS = 600
MAX_WATCH_ITEMS = 10

STOP_GOAL = "goal_reached"
STOP_TURNS = "turn_budget_exhausted"
STOP_DEADLINE = "deadline_reached"#: The charter's ``stop_on: first_finding``. A fifth stop, and the only one
#: step 4 wires: ``coverage_plateau`` is persisted and reported and behaves as
#: ``budget``, because nothing detects a plateau yet -- nothing grades a
#: plateau stop, follow-up.
STOP_FINDING = "first_finding"
#: The charter's ``destructive`` REFUSE, recorded as a stop. A refused run is
#: OVER: ``session._submit_explore`` writes this the moment the executor
#: reports a refusal, so ``stop_reason`` -- which returns any non-empty
#: ``state["stop"]`` -- and every page under it say the run stopped, rather
#: than leaving a terminal refusal looking like a turn the tester can answer.
STOP_GUARD_REFUSED = "guard_refused"

#: What the tester is TOLD when that happens, in the SAME reply. The lane's
#: rule: a site that refuses says so BY NAME and leaves no success-shaped
#: message behind it.
GUARD_REFUSED_NOTICE = (
    "This run's charter says `destructive: none`, so the guard's stop ENDED "
    "the attempt rather than pausing for you. Nothing was tapped and the run "
    "is over. Start a run with `destructive: reversible` if a stop should "
    "pause for a confirmation instead."
)
RUNNING = "running"

EXTENSION_REFUSAL = (
    "An extension needs a stated reason naming what is still unexplored. "
    "Nothing was extended."
)

#: Shown when a turn reply carried no ``finding``, although the packet's
#: ``worker_instructions`` asks for one EVERY turn.
#:
#: It is a DISCLOSURE and not a refusal, and that is the deliberate choice: by
#: the time this runs the turn's actions have already been replayed on the
#: device, so refusing the turn would report a device that DID move as a turn
#: that did not, and boomeranging for a prose field would spend one of the
#: session's stops on it. The report says the same thing from the other end --
#: it counts the turns that recorded nothing -- so the gap stays visible even
#: if this line is ignored in chat.
NO_FINDING_NOTICE = (
    "This turn recorded no `finding`, so the report has nothing to show for it. "
    "Add one sentence next turn, even when the turn went fine."
)
EXTENSION_SPENT = (
    "This session has already used its one extension, so the budget stands. "
    "Report what was found and stop."
)

#: Shown when an extension would gain NOTHING, because the charter's own
#: budget is already this run's ceiling. It is a REFUSAL BY NAME, and the
#: session's one extension is NOT spent.
#:
#: Measured on the shipped code before this existed: for ANY charter naming a
#: steps or minutes budget the extension was a COMPLETE no-op -- new_state had
#: already clamped the budget to the charter's figure and the extension
#: re-clamped to that same figure -- while the reply said "Extended once by 15
#: turns and 10 minutes" and the one extension was consumed. The tester's
#: model then plans against fifteen turns it does not have, and the run dies
#: at the next budget check with nothing in the record explaining why.
#:
#: The rule this obeys is the lane's, not this function's: EVERY site that
#: refuses or reduces what the model asked for says so BY NAME in the same
#: reply, and no clamp leaves a success-shaped message behind it -- the same
#: reason a flag-OFF ``apply=true`` on the push path refuses by name rather
#: than silently downgrading to a dry run.
EXTENSION_NOT_GRANTED = (
    "This run's budget comes from its charter, which is already the ceiling, "
    "so an extension would add no turns and no time: nothing was extended and "
    "the session's one extension was NOT spent. Report what was found and "
    "stop, or start a run whose charter names a larger budget."
)


def _now(value: float | None) -> float:
    return time.time() if value is None else float(value)


def new_state(
    goal: object,
    watch_for: object = (),
    *,
    guard: bool = True,
    now: float | None = None,
    charter: object = None,
) -> dict:
    """A fresh, JSON-serialisable session state. Stored in the run manifest.

    *charter* is the run's TERMS (``tools/mobile/charter.py``). It lives HERE,
    inside the state, rather than in a manifest key of its own, because the
    state IS ``manifest["explore"]`` and is already the dict a turn packet is
    built from -- so a resume in another chat explores under the same terms
    with no new reader and no new writer. ``charter=None`` normalises to the
    default charter and reproduces this function's previous values exactly.
    """
    started = _now(now)
    terms = charter_mod.normalize(charter)
    # DOWNWARD ONLY. The charter can SHORTEN a run and can never lengthen one:
    # MAX_TURNS and DEADLINE_S remain the lane's ceilings, and a charter asking
    # for more gets them. See charter.budget_for.
    steps, seconds = charter_mod.budget_for(terms, MAX_TURNS, DEADLINE_S)
    items = []
    for item in list(watch_for or [])[:MAX_WATCH_ITEMS]:
        text = " ".join(str(item or "").split())[:200]
        if text:
            items.append(text)
    return {
        "goal": " ".join(str(goal or "").split())[:MAX_GOAL_CHARS],
        "watch_for": items,
        "turn": 0,
        # The last turn number a replay actually SPENT. ``turn`` is the highest
        # number ALLOCATED and keeps that meaning for every reader it already
        # has; this is a second value with a second name, and its one job is to
        # decide which number the next packet may re-use. See ``next_turn``.
        "committed_turn": 0,
        "turns_budget": steps,
        "started": started,
        "deadline": started + seconds,
        "extensions_used": 0,
        "guard": bool(guard),
        "charter": terms,
        "stop": "",
        "findings": [],
    }


def stop_reason(state: object, *, now: float | None = None) -> str:
    """Why this session must stop, or ``""``. Checked BEFORE each turn.

    THE STRICT ONE, and its strictness is load-bearing. It coerces ``turn``,
    ``turns_budget`` and ``deadline``, and a state whose numbers are junk makes
    it RAISE. That raise is the containment gate: ``session.resolve`` calls this
    at the first statement of every entry point, its own handler turns the
    ValueError into ``{"error": ...}``, and so a corrupt state is refused before
    ``next_packet``, ``submit`` or ``qa_mobile_status`` can reach a device.
    ``test_l11_...`` and ``test_l12_...`` in
    ``tests/mobile/test_mobile_explore_turn_ledger.py`` pin exactly that -- L12
    states that the replay and the commit line are never reached.

    So DO NOT make this total. A render must never fail to draw a page because a
    field on disk is junk, and a gate must refuse exactly then: that is two
    contracts, and two contracts need two names over one core.
    :func:`display_stop` is the other name.
    """
    body = state if isinstance(state, dict) else {}
    if str(body.get("stop") or ""):
        return str(body["stop"])
    if int(body.get("turn") or 0) >= int(body.get("turns_budget") or MAX_TURNS):
        return STOP_TURNS
    if _now(now) >= float(body.get("deadline") or 0):
        return STOP_DEADLINE
    return ""


def display_stop(manifest: object, *, now: float | None = None) -> str:
    """Why the run this MANIFEST describes has stopped, or ``""``. TOTAL.

    THE RENDER CONTRACT, and the counterpart to :func:`stop_reason`'s strict
    one. Same core -- it calls ``stop_reason`` -- with two differences that are
    the contract:

    * it takes the MANIFEST and does the lane check itself, so a consumer cannot
      get "is this an explore run, and did it stop" half right;
    * it NEVER raises. A state whose numbers will not coerce reads as NOT
      stopped, which is the answer the page gave before any of this existed. The
      alternative is measured and worse: ``report._is_partial`` calls this, and
      ``report.render``'s outer ``except`` turns any raise into NO PAGE AT ALL.

    Its callers are a page and a coverage count. The GATE keeps ``stop_reason``.
    """
    body = manifest if isinstance(manifest, dict) else {}
    if str(body.get("lane") or "") != "explore":
        return ""
    explore = body.get("explore")
    try:
        return stop_reason(explore if isinstance(explore, dict) else {}, now=now)
    except (TypeError, ValueError, OverflowError):
        logger.warning(
            "mobile.explore_runner: unreadable explore state, displaying as running"
        )
        return ""


def remaining(state: object, *, now: float | None = None) -> dict:
    body = state if isinstance(state, dict) else {}
    turns = max(
        0, int(body.get("turns_budget") or MAX_TURNS) - int(body.get("turn") or 0)
    )
    seconds = max(0, int(float(body.get("deadline") or 0) - _now(now)))
    return {"turns": turns, "seconds": seconds}


async def next_turn(
    run_id: str, state: object, ctx: object, *, now: float | None = None
) -> dict:
    """Dump, prune and build the turn packet -- or stop.

    ``{"error", "content": {"status", "state", "screen", "packet", "remaining"}}``
    with ``status`` one of ``running`` / the three stop reasons.
    """
    try:
        body = dict(state if isinstance(state, dict) else {})
        stop = stop_reason(body, now=now)
        if stop:
            body["stop"] = stop
            return {
                "error": None,
                "content": {
                    "status": stop,
                    "state": body,
                    "screen": None,
                    "packet": None,
                    "remaining": remaining(body, now=now),
                },
            }

        dumped = await adb.uiautomator_dump(getattr(ctx, "serial", ""))
        if dumped.get("error"):
            return dumped
        sized = await adb.display_size(getattr(ctx, "serial", ""))
        # Same device fact, same cache, same reason as the case lane's.
        dpi = await adb.display_density(getattr(ctx, "serial", ""))
        pruned = composite.observe(
            dumped.get("content"),
            # ONE producer, shared with the replay: see resolve_activity.
            await executor.resolve_activity(ctx),
            display=sized.get("content"),
            density=dpi.get("content"),
        )
        if pruned.get("error"):
            return pruned
        screen = pruned.get("content") or {}

        # Same contract as case_runner's: a screen the report can draw,
        # stored best-effort, never able to stop a turn.
        run_store.write_screen(run_id, screen)
        # THE SCOPE VERDICT, COMPUTED ONCE. The packet builder READS
        # ``state["scope_verdict"]`` and never re-derives it: one writer, one
        # reader, so what the model is told and what the run records cannot
        # disagree. Three-valued and POSITIVE -- see charter.scope_verdict.
        verdict = charter_mod.scope_verdict(body.get("charter"), screen)
        body["scope_verdict"] = verdict
        if verdict.get("state") == charter_mod.SCOPE_OUT:
            # RECORDED, never silently explored. The run is NOT stopped here:
            # nothing grades a scope-driven stop because nothing implements
            # one -- follow-up. The list is bounded, because it lands in a
            # manifest that a report reads.
            # ONE producer of the record, which owns BOTH the identity rule
            # (one entry per screen_id + matched line, not one per turn the
            # model lingered) and the cap. The same screen is seen on every
            # turn until the model leaves it, so an unconditional append
            # recorded one fact many times -- measured.
            body["off_charter"] = charter_mod.record_off_charter(
                body.get("off_charter"),
                {
                    "turn": int(body.get("turn") or 0) + 1,
                    "screen_id": str(screen.get("screen_id") or "")[:80],
                    "matched": str(verdict.get("matched") or "")[:200],
                },
            )

        # A turn number is consumed by a replay HAVING HAPPENED, not by a packet
        # having gone out. This increment was unconditional and
        # ``session.next_packet`` persists the result immediately, so the number
        # was durable the moment the packet was built: run
        # mrun-20260905-051728-bd1777 shows TC-001, TC-002, TC-004 with its third
        # turn titled "turn 4" -- the refusal had no number to decline, because
        # the number was already spent on disk.
        #
        # So the number handed out here is PROVISIONAL. It is re-derived
        # identically on every packet build until a submit that actually replayed
        # something commits it (``session._submit_explore``), which is also what
        # keeps ``session.explore_turn_tc_id`` stable across a refused script or a
        # resumed chat -- ``committed + 1`` is a pure function of a value that did
        # not change.
        #
        # ``turn`` keeps its MEANING -- the highest number allocated -- so
        # ``stop_reason``, ``remaining`` and everything downstream of them (the
        # packet's ``turns_left``, the renderer, the report) are unchanged in code
        # and read it exactly as before. Its VALUE differs from the old behaviour
        # in TWO places, both deliberate. First: a REFUSED turn no longer advances the
        # ladder, so ``turns_left`` reads 2, 2, 2 across two refusals where it
        # used to read 3, 2, 1. That is the correction -- the ladder bounds work
        # done ON THE DEVICE and a refused script does none -- and it means the
        # bound on a model that keeps emitting malformed scripts is the
        # wall-clock deadline rather than ``turns_budget``. Second: a refusal on
        # the LAST rung still ends the run, so that number is never committed and
        # no record is written for it -- the run stops one card short rather than
        # writing a card for a turn that did nothing. Pinned by ``test_l8``, whose
        # docstring states it; this comment used to omit it.
        #
        # This function is also the MIGRATION writer, and the write below is
        # UNCONDITIONAL on every build by design -- do not "simplify" it into a
        # write-only-when-absent. The line after it bumps ``turn``, so a seed
        # that was derived and not written back would be re-derived from the
        # bumped value on the next build and drift one higher on every re-issue
        # of a legacy run's packet. Writing the same value again is idempotent;
        # not writing it is a numbering leak.
        #
        # An ABSENT key is a state written before this ledger existed and is read
        # as fully committed. A CORRUPT key falls back to the same place, never
        # to 0: a live run at turn 7 restarted at TC-001 would have
        # ``run_store.write_case`` overwrite the cards it already wrote. If
        # ``turn`` is junk too this raises and ``next_turn``'s handler answers
        # with an error -- the same answer ``stop_reason`` above already gives for
        # that state, which is why there is no third fallback here.
        committed = body.get("committed_turn")
        if committed is None:
            committed = body.get("turn")
        try:
            committed = int(committed or 0)
        except (TypeError, ValueError, OverflowError):
            committed = int(body.get("turn") or 0)
        body["committed_turn"] = committed
        body["turn"] = committed + 1
        left = remaining(body, now=now)

        from agents import mobile_run

        packet = mobile_run.build_explore_turn(
            body, screen, run_id=run_id, remaining=left
        )
        return {
            "error": None,
            "content": {
                "status": RUNNING,
                "state": body,
                "screen": screen,
                "packet": packet,
                "remaining": left,
            },
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.explore_runner.next_turn failed")
        return {"error": str(exc), "content": None}


def apply_turn_result(state: object, raw: object, *, now: float | None = None) -> dict:
    """Fold a turn reply into the state: goal, a finding, or an extension.

    ``{"error", "content": {"state", "status", "notice"}}``. ``notice`` is the
    line a handler shows when an extension was refused -- refusing silently
    would read as the model's request having been granted.
    """
    try:
        body = dict(state if isinstance(state, dict) else {})
        reply = raw if isinstance(raw, dict) else {}
        notice = ""

        finding = " ".join(str(reply.get("finding") or "").split())[:600]
        if finding:
            findings = list(body.get("findings") or [])
            findings.append({"turn": int(body.get("turn") or 0), "note": finding})
            body["findings"] = findings[:MAX_TURNS]
        else:
            # Disclosed, never refused -- see NO_FINDING_NOTICE. An extension
            # verdict overwrites this below, on purpose: that one is about the
            # budget, which matters more than a nudge.
            notice = NO_FINDING_NOTICE

        if bool(reply.get("goal_reached")):
            body["stop"] = STOP_GOAL
            return {
                "error": None,
                "content": {"state": body, "status": STOP_GOAL, "notice": notice},
            }

        # ANCHOR NOTE: this text is inserted by REPLACING the statement that
        # FOLLOWS the ``if bool(reply.get("goal_reached")):`` block -- the
        # ``requested = ...`` assignment, a SIBLING at function-body
        # indentation -- and not by anchoring on that if-block's ``return``.
        # Anchoring on the return placed this text INSIDE the if-body, after an
        # unconditional return, where it was dead code and ``stop_on:
        # first_finding`` never fired at all. A following sibling statement is
        # the anchor that provably lands outside the block: its own indentation
        # IS the insertion point, so the block cannot be swallowed by the
        # branch above it. The suite caught the dead placement in one run.
        # THE CHARTER'S STOP CONDITION, deliberately BELOW the
        # ``goal_reached`` return and not above it. A turn may report BOTH
        # a finding and ``goal_reached``; measured on the ordering this
        # replaces, that run returned ``first_finding`` and ``stop_reason``
        # then named the stop as ``first_finding`` for a run whose goal was
        # actually REACHED -- a false statement about the run in the
        # artifact whose purpose is reconstructing it. Goal-reached is the
        # stronger and more informative outcome, and the finding is still
        # recorded in ``body["findings"]`` above either way, so precedence
        # is expressed STRUCTURALLY by this order rather than by a second
        # condition that could drift from the return above it. Ordering
        # mutant M15 swaps the two blocks back;
        # test_a_turn_that_reports_both_a_finding_and_the_goal_says_goal_reached
        # is the pin that goes red.
        #
        # ``stop`` is the state's own existing stop field and
        # ``stop_reason`` remains its ONLY reader, so this adds a writer
        # and not a second derivation.
        if finding and charter_mod.stop_on(body.get("charter")) == "first_finding":
            body["stop"] = STOP_FINDING
            return {
                "error": None,
                "content": {
                    "state": body,
                    "status": STOP_FINDING,
                    "notice": notice,
                },
            }

        requested = reply.get("request_extension")
        if requested:
            reason = " ".join(str(reply.get("extension_reason") or "").split())[:400]
            if int(body.get("extensions_used") or 0) >= MAX_EXTENSIONS:
                notice = EXTENSION_SPENT
            elif not reason:
                notice = EXTENSION_REFUSAL
            else:
                # THE SECOND WRITER of this run's budget. ``new_state``
                # clamped these two numbers through ``charter.budget_for``;
                # this site ADDS to them, so "a charter can never lengthen a
                # run" is false here unless it re-clamps. ``budget_cap`` is
                # the one producer of what the tester asked for and returns 0
                # where they asked for nothing, so a run with no charter
                # extends exactly as it always has.
                cap_turns, cap_seconds = charter_mod.budget_cap(
                    body.get("charter")
                )
                had_turns = int(body.get("turns_budget") or MAX_TURNS)
                turns = had_turns + EXTENSION_TURNS
                if cap_turns:
                    turns = min(turns, min(cap_turns, MAX_TURNS))
                had_deadline = float(body.get("deadline") or _now(now))
                deadline = had_deadline + EXTENSION_S
                if cap_seconds:
                    # ``is not None``, never ``or``: ``started`` is 0.0 on
                    # every test clock and in any run whose epoch is 0, and
                    # the ``or`` form silently REBASES the deadline onto NOW
                    # -- lengthening the very run this clamp exists to bound.
                    # That is mutant M6c, killed by
                    # test_an_extension_cannot_outrun_the_charters_budget,
                    # which builds the state at now=0.0 and asserts the
                    # deadline stays at or below 120.0. Do not "tidy" it back.
                    _started = body.get("started")
                    started = float(_started if _started is not None else _now(now))
                    deadline = min(
                        deadline, started + min(cap_seconds, DEADLINE_S)
                    )
                gained_turns = turns - had_turns
                gained_seconds = deadline - had_deadline
                if gained_turns <= 0 and gained_seconds <= 0:
                    # REFUSE BY NAME. The clamp above left BOTH numbers where
                    # they were, so granting here would consume the session's
                    # one extension, change nothing, and REPORT a gain of 15
                    # turns and 10 minutes -- measured, and for EVERY charter
                    # naming a steps or minutes budget, because ``new_state``
                    # already clamped to that same figure. A success-shaped
                    # reply for work that did not happen is the worse failure:
                    # the model plans against turns it does not have and the
                    # run dies at the next budget check with nothing in the
                    # record explaining why. So nothing is written,
                    # ``extensions_used`` is NOT incremented,
                    # ``extension_reason`` is left unwritten, and the refusal
                    # names the ceiling that bound it. Mutant M6f deletes this
                    # branch; the upper-bound assertions in
                    # test_an_extension_cannot_outrun_the_charters_budget
                    # CANNOT see it, because an inequality is satisfied by the
                    # collapse -- the pin asserts this notice and
                    # ``extensions_used`` instead.
                    notice = EXTENSION_NOT_GRANTED
                else:
                    body["extensions_used"] = (
                        int(body.get("extensions_used") or 0) + 1
                    )
                    body["turns_budget"] = turns
                    body["deadline"] = deadline
                    body["extension_reason"] = reason
                    # WHAT WAS ACTUALLY GRANTED, never the full figure. A
                    # PARTIAL grant (one axis moves, the other does not) names
                    # only the axis that moved, for the same reason the clamp
                    # sentence in ``charter.describe_terms`` names only the
                    # axis that clamped: printing a number the run did not get
                    # is a false statement about the run the tester just had.
                    granted = []
                    if gained_turns > 0:
                        granted.append(str(int(gained_turns)) + " turns")
                    if gained_seconds >= 60:
                        granted.append(
                            str(int(gained_seconds // 60)) + " minutes"
                        )
                    elif gained_seconds > 0:
                        granted.append("under a minute")
                    notice = (
                        "Extended once by "
                        + " and ".join(granted)
                        + ": "
                        + reason
                    )

        status = stop_reason(body, now=now) or RUNNING
        body["stop"] = status if status != RUNNING else ""
        return {
            "error": None,
            "content": {"state": body, "status": status, "notice": notice},
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.explore_runner.apply_turn_result failed")
        return {"error": str(exc), "content": None}
