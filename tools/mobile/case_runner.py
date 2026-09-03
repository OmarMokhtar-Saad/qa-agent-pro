"""One test case's lifecycle: clean launch, plan, replay, verdict, checkpoint.

Two invariants this module exists to hold, both mutation-proofed:

1. **``force_stop`` then ``launch`` before EVERY case.** A case that inherits the
   previous case's half-open dialog is not the case the tester wrote, and the
   failure it reports is a lie. Login survives, because ``force-stop`` kills the
   process and not its data.
2. **At most ``MAX_ESCAPES`` boomerangs per case, then ``blocked``.** The escape
   hatch is what makes plan-then-replay workable, and an uncapped escape hatch is
   an infinite loop that spends the tester's tokens. The counter lives in the
   CHECKPOINT, not in memory, so resuming in a fresh chat cannot reset it -- the
   MCP server restarts on every ``.env`` edit and in-memory would mean "escape
   forever, one restart at a time".
"""

from __future__ import annotations

import logging
import time

from tools.mobile import actions as actions_mod
from tools.mobile import adb, executor, perception, run_store

logger = logging.getLogger(__name__)

MAX_ESCAPES = 3

VERDICT_PASS = "pass"
VERDICT_FAIL = "fail"
VERDICT_BLOCKED = "blocked"
NEEDS_MODEL = "needs_model"
NEEDS_TESTER = "needs_tester"

ESCAPE_CAP_REASON = (
    "This case was handed back to the planner "
    + str(MAX_ESCAPES)
    + " times and still could not be completed, so it is recorded as blocked "
    "rather than retried again. The trace shows how far it got."
)


def case_view(case: object) -> dict:
    """A ``TestCase`` (or a dict shaped like one) reduced to what a packet needs.

    Deliberately NOT the model dump: risk fields, stable ids and automation
    status say nothing to a planner and every byte competes with the screen.
    """
    try:
        if hasattr(case, "model_dump"):
            body = case.model_dump(mode="json")
        else:
            body = dict(case or {})
        steps = []
        for step in body.get("steps") or []:
            if not isinstance(step, dict):
                continue
            steps.append(
                {
                    "step_number": int(step.get("step_number") or 0),
                    "action": str(step.get("action") or "")[:600],
                    "test_data": str(step.get("test_data") or "")[:300],
                    "expected_result": str(step.get("expected_result") or "")[:600],
                }
            )
        return {
            "tc_id": str(body.get("tc_id") or ""),
            "title": str(body.get("title") or "")[:250],
            "module": str(body.get("module") or "")[:100],
            "priority": str(body.get("priority") or ""),
            "type": str(body.get("type") or ""),
            "preconditions": str(body.get("preconditions") or "")[:600],
            "steps": steps,
        }
    except Exception:  # pragma: no cover - defensive
        logger.exception("mobile.case_runner.case_view failed")
        return {"tc_id": "", "title": "", "steps": []}


def escapes_used(run_id: str, tc_id: str) -> int:
    """How many times this case has already boomeranged, read from disk."""
    body = (run_store.read_case(run_id, tc_id) or {}).get("content")
    if not isinstance(body, dict):
        return 0
    try:
        return max(0, int(body.get("escapes") or 0))
    except (TypeError, ValueError):
        return 0


async def start_case(run_id: str, case: object, ctx: executor.Context) -> dict:
    """Clean-launch the app, dump the first screen, build the planning packet.

    ``{"error", "content": {"tc_id", "screen", "packet", "escapes"}}``.
    """
    try:
        view = case_view(case)
        tc_id = view.get("tc_id") or ""
        if not run_store.valid_tc_id(tc_id):
            return {
                "error": (
                    "Refusing to run case id "
                    + repr(str(tc_id)[:40])
                    + "; it must look like TC-001."
                ),
                "content": None,
            }
        if not ctx.package:
            return {
                "error": "No app package is set for this run; nothing was launched.",
                "content": None,
            }

        # Invariant 1. Unconditional, and in this order.
        stopped = await adb.force_stop(ctx.serial, ctx.package)
        if stopped.get("error"):
            return stopped
        launched = await adb.launch(ctx.serial, ctx.package)
        if launched.get("error"):
            return launched

        dumped = await adb.uiautomator_dump(ctx.serial)
        if dumped.get("error"):
            return dumped
        pruned = perception.prune(dumped.get("content"), ctx.activity)
        if pruned.get("error"):
            return pruned
        screen = pruned.get("content") or {}

        # The report joins a trace's screen ids to this library. A failure
        # here may never change a verdict, so the result is deliberately
        # not read: a lost wireframe beats a lost verdict.
        run_store.write_screen(run_id, screen)

        from agents import mobile_run

        used = escapes_used(run_id, tc_id)
        packet = mobile_run.build_case_job(
            view, screen, run_id=run_id, tc_id=tc_id, escapes=used
        )
        run_store.write_case(
            run_id,
            tc_id,
            {
                "tc_id": tc_id,
                "title": view.get("title") or "",
                "verdict": "",
                "status": "planning",
                "escapes": used,
                "screen_id": screen.get("screen_id") or "",
                "started": time.time(),
            },
        )
        return {
            "error": None,
            "content": {
                "tc_id": tc_id,
                "screen": screen,
                "packet": packet,
                "escapes": used,
            },
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.case_runner.start_case failed")
        return {"error": str(exc), "content": None}


async def submit_case(
    run_id: str,
    case: object,
    ctx: executor.Context,
    raw_script: object,
    *,
    screen: dict | None = None,
) -> dict:
    """Validate a planner's script, replay it, decide a verdict, checkpoint.

    ``{"error", "content": {"tc_id", "verdict", "status", "reason", "trace",
    "escapes", "packet"}}``. ``packet`` is the NEXT thing to ask for -- an
    escape-hatch job on ``needs_model``, a credential request on
    ``needs_tester`` -- and ``None`` once the case is terminal.
    """
    try:
        view = case_view(case)
        tc_id = view.get("tc_id") or ""
        if not run_store.valid_tc_id(tc_id):
            return {"error": "Invalid case id.", "content": None}

        parsed = actions_mod.parse_script(raw_script)
        if parsed.get("error"):
            # A refused script is NOT an escape: nothing was replayed, so the
            # planner gets the same screen back and one of its escapes is not
            # spent on our own validation.
            return {
                "error": None,
                "content": _checkpoint(
                    run_id,
                    tc_id,
                    view,
                    verdict="",
                    status=NEEDS_MODEL,
                    reason=str(parsed["error"]),
                    trace=[],
                    escapes=escapes_used(run_id, tc_id),
                    packet=None,
                ),
            }

        ctx.screen = screen if isinstance(screen, dict) else None
        replayed = await executor.replay(parsed["content"], ctx)
        if replayed.get("error"):
            return replayed
        result = replayed.get("content") or {}
        status = str(result.get("status") or "")
        trace = list(result.get("trace") or [])
        new_screen = (
            result.get("screen") if isinstance(result.get("screen"), dict) else {}
        )
        used = escapes_used(run_id, tc_id)
        if new_screen:
            run_store.write_screen(run_id, new_screen)

        from agents import mobile_run

        if status == executor.STATUS_DONE:
            verdict = str(result.get("verdict") or "") or VERDICT_PASS
            if verdict not in (VERDICT_PASS, VERDICT_FAIL, VERDICT_BLOCKED):
                verdict = VERDICT_BLOCKED
            return {
                "error": None,
                "content": _checkpoint(
                    run_id,
                    tc_id,
                    view,
                    verdict=verdict,
                    status=executor.STATUS_DONE,
                    reason=str(result.get("reason") or ""),
                    trace=trace,
                    escapes=used,
                    packet=None,
                ),
            }

        if status == executor.STATUS_NEEDS_TESTER:
            packet = mobile_run.build_tester_request(
                str(result.get("field") or ""),
                str(result.get("reason") or ""),
                run_id=run_id,
                tc_id=tc_id,
                guard_term=str(result.get("guard_term") or ""),
            )
            return {
                "error": None,
                "content": _checkpoint(
                    run_id,
                    tc_id,
                    view,
                    verdict="",
                    status=NEEDS_TESTER,
                    reason=str(result.get("reason") or ""),
                    trace=trace,
                    escapes=used,
                    packet=packet,
                ),
            }

        if status == executor.STATUS_ERROR:
            return {
                "error": None,
                "content": _checkpoint(
                    run_id,
                    tc_id,
                    view,
                    verdict=VERDICT_BLOCKED,
                    status=executor.STATUS_ERROR,
                    reason=str(result.get("reason") or ""),
                    trace=trace,
                    escapes=used,
                    packet=None,
                ),
            }

        # needs_model -- the escape hatch. Invariant 2.
        used += 1
        if used >= MAX_ESCAPES:
            return {
                "error": None,
                "content": _checkpoint(
                    run_id,
                    tc_id,
                    view,
                    verdict=VERDICT_BLOCKED,
                    status=VERDICT_BLOCKED,
                    reason=ESCAPE_CAP_REASON
                    + " Last stop: "
                    + str(result.get("reason") or ""),
                    trace=trace,
                    escapes=used,
                    packet=None,
                ),
            }
        packet = mobile_run.build_escape_job(
            view,
            new_screen,
            trace,
            run_id=run_id,
            tc_id=tc_id,
            escapes=used,
            reason=str(result.get("reason") or ""),
        )
        return {
            "error": None,
            "content": _checkpoint(
                run_id,
                tc_id,
                view,
                verdict="",
                status=NEEDS_MODEL,
                reason=str(result.get("reason") or ""),
                trace=trace,
                escapes=used,
                packet=packet,
            ),
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.case_runner.submit_case failed")
        return {"error": str(exc), "content": None}


def _checkpoint(
    run_id: str,
    tc_id: str,
    view: dict,
    *,
    verdict: str,
    status: str,
    reason: str,
    trace: list,
    escapes: int,
    packet: object,
) -> dict:
    """Write the case checkpoint and return the caller's payload.

    The trace entries were already redacted by ``executor``; ``run_store``
    redacts again on the way to disk. Two layers on purpose: this payload is
    ALSO returned to the caller and rendered, and only one of those two paths
    goes through the store.
    """
    body = {
        "tc_id": tc_id,
        "title": view.get("title") or "",
        "verdict": verdict,
        "status": status,
        "reason": str(reason or "")[:1200],
        "trace": trace,
        "escapes": int(escapes),
        "updated": time.time(),
    }
    written = run_store.write_case(run_id, tc_id, body)
    payload = dict(body)
    payload["packet"] = packet
    payload["checkpoint_error"] = written.get("error")
    return payload
