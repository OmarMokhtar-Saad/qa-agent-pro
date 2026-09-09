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

from tools.mobile import adb, perception, run_store

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
STOP_DEADLINE = "deadline_reached"
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


def _now(value: float | None) -> float:
    return time.time() if value is None else float(value)


def new_state(
    goal: object,
    watch_for: object = (),
    *,
    guard: bool = True,
    now: float | None = None,
) -> dict:
    """A fresh, JSON-serialisable session state. Stored in the run manifest."""
    started = _now(now)
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
        "turns_budget": MAX_TURNS,
        "started": started,
        "deadline": started + DEADLINE_S,
        "extensions_used": 0,
        "guard": bool(guard),
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
        pruned = perception.prune(
            dumped.get("content"),
            str(getattr(ctx, "activity", "") or ""),
            display=sized.get("content"),
            density=dpi.get("content"),
        )
        if pruned.get("error"):
            return pruned
        screen = pruned.get("content") or {}

        # Same contract as case_runner's: a screen the report can draw,
        # stored best-effort, never able to stop a turn.
        run_store.write_screen(run_id, screen)

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

        requested = reply.get("request_extension")
        if requested:
            reason = " ".join(str(reply.get("extension_reason") or "").split())[:400]
            if int(body.get("extensions_used") or 0) >= MAX_EXTENSIONS:
                notice = EXTENSION_SPENT
            elif not reason:
                notice = EXTENSION_REFUSAL
            else:
                body["extensions_used"] = int(body.get("extensions_used") or 0) + 1
                body["turns_budget"] = (
                    int(body.get("turns_budget") or MAX_TURNS) + EXTENSION_TURNS
                )
                body["deadline"] = (
                    float(body.get("deadline") or _now(now)) + EXTENSION_S
                )
                body["extension_reason"] = reason
                notice = (
                    "Extended once by "
                    + str(EXTENSION_TURNS)
                    + " turns and "
                    + str(EXTENSION_S // 60)
                    + " minutes: "
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
