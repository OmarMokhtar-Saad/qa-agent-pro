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
        "turns_budget": MAX_TURNS,
        "started": started,
        "deadline": started + DEADLINE_S,
        "extensions_used": 0,
        "guard": bool(guard),
        "stop": "",
        "findings": [],
    }


def stop_reason(state: object, *, now: float | None = None) -> str:
    """Why this session must stop, or ``""``. Checked BEFORE each turn."""
    body = state if isinstance(state, dict) else {}
    if str(body.get("stop") or ""):
        return str(body["stop"])
    if int(body.get("turn") or 0) >= int(body.get("turns_budget") or MAX_TURNS):
        return STOP_TURNS
    if _now(now) >= float(body.get("deadline") or 0):
        return STOP_DEADLINE
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
        pruned = perception.prune(
            dumped.get("content"), str(getattr(ctx, "activity", "") or "")
        )
        if pruned.get("error"):
            return pruned
        screen = pruned.get("content") or {}

        # Same contract as case_runner's: a screen the report can draw,
        # stored best-effort, never able to stop a turn.
        run_store.write_screen(run_id, screen)

        body["turn"] = int(body.get("turn") or 0) + 1
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
