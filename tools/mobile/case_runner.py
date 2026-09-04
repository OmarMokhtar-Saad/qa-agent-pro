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
from tools.mobile_evidence import capture

logger = logging.getLogger(__name__)

MAX_ESCAPES = 3

VERDICT_PASS = "pass"
VERDICT_FAIL = "fail"
VERDICT_BLOCKED = "blocked"
#: Ran, but proved nothing: no assert passed and no action moved the screen.
#: Deliberately NOT folded into `blocked`, which means the case could not be
#: attempted -- this one WAS attempted and produced no evidence, and a tester
#: reading "blocked" would go looking for an obstacle that does not exist.
VERDICT_UNVERIFIED = "unverified"
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
        # App evidence (plan D5): the ring buffer is cleared BETWEEN the stop
        # and the launch, so a slice begins with the app's own start-up. With
        # no profile for this package, or the flag off, ``begin_case`` makes
        # no adb call and says why; a failure here never blocks the case.
        evidence = _evidence_record(
            await capture.begin_case(ctx.serial, ctx.package, run_id, tc_id)
        )
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
                "evidence": evidence,
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
        # App evidence (plan D5): ONE slice per replay, after the trace is
        # final and before the checkpoint that carries the record forward.
        # The refused-script path above never reaches here: nothing ran, so
        # there is nothing to slice.
        await _slice_evidence(run_id, tc_id, ctx)

        from agents import mobile_run

        if status == executor.STATUS_DONE:
            verdict = str(result.get("verdict") or "") or VERDICT_PASS
            if verdict not in (
                VERDICT_PASS,
                VERDICT_FAIL,
                VERDICT_BLOCKED,
                VERDICT_UNVERIFIED,
            ):
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
    # Carried FORWARD from the planning checkpoint, which this body replaces
    # whole: the case's start time and its evidence record (plan D6). Without
    # this, ``started`` survived only until the first submit -- and the join
    # window needs both ends.
    prior = (run_store.read_case(run_id, tc_id) or {}).get("content")
    prior = prior if isinstance(prior, dict) else {}
    now = time.time()
    body = {
        "tc_id": tc_id,
        "title": view.get("title") or "",
        "verdict": verdict,
        "status": status,
        "reason": str(reason or "")[:1200],
        "trace": trace,
        "escapes": int(escapes),
        "started": prior.get("started") or now,
        "updated": now,
        "evidence": _evidence_record(prior.get("evidence")),
    }
    written = run_store.write_case(run_id, tc_id, body)
    payload = dict(body)
    payload["packet"] = packet
    payload["checkpoint_error"] = written.get("error")
    return payload


def _evidence_record(source: object) -> dict:
    """The case's evidence record, normalised from ``capture.begin_case``'s
    reply OR from a record already on disk. Every key present, so the report
    never reads a missing one, and the slice counter is an int."""
    holder = source if isinstance(source, dict) else {}
    body = holder.get("content") if "content" in holder else holder
    body = body if isinstance(body, dict) else {}
    try:
        slices = max(0, int(body.get("slices") or 0))
    except (TypeError, ValueError):
        slices = 0
    written = body.get("slices_written")
    return {
        "profile": body.get("profile"),
        "clock_offset_ms": body.get("clock_offset_ms"),
        "pid": body.get("pid"),
        "skipped": body.get("skipped") or holder.get("error") or None,
        "slices": slices,
        "slices_written": list(written) if isinstance(written, list) else [],
    }


async def _slice_evidence(run_id: str, tc_id: str, ctx: executor.Context) -> None:
    """Take this replay's logcat slice and record it on the case (plan D5).

    Reads the record ``start_case`` left, asks ``capture`` for the slice --
    which redacts the tester's typed values BEFORE anything reaches disk (plan
    D4) -- and writes the updated record back so the checkpoint that follows
    carries it forward. A skipped or failed slice is stored as such; it can
    never change a verdict, which is why every exception ends here.
    """
    try:
        prior = (run_store.read_case(run_id, tc_id) or {}).get("content")
        prior = prior if isinstance(prior, dict) else {}
        evidence = _evidence_record(prior.get("evidence"))
        if evidence.get("skipped"):
            return
        # H2: the typed values must ALSO be known at run end, when the app's own
        # event log is pulled; remembered in memory only, never on disk.
        capture.remember_typed(run_id, (ctx.tester_inputs or {}).values())
        sliced = await capture.slice_case(
            ctx.serial,
            ctx.package,
            run_id,
            tc_id,
            evidence["slices"],
            begin=evidence,
            tester_inputs=ctx.tester_inputs,
        )
        content = sliced.get("content") if isinstance(sliced, dict) else None
        content = content if isinstance(content, dict) else {}
        evidence["slices"] += 1
        evidence["slices_written"].append(
            {
                "index": evidence["slices"] - 1,
                "path": str(content.get("path") or ""),
                "lines": content.get("lines"),
                "truncated": bool(content.get("truncated")),
                "skipped": content.get("skipped")
                or (sliced.get("error") if isinstance(sliced, dict) else None),
            }
        )
        # NEVER write a document read before an await. A checkpoint may have
        # landed while the device was being read; the fresh copy carries its
        # verdict and trace, and only the evidence record is spliced in.
        fresh = (run_store.read_case(run_id, tc_id) or {}).get("content")
        fresh = fresh if isinstance(fresh, dict) else prior
        fresh["evidence"] = evidence
        run_store.write_case(run_id, tc_id, fresh)
    except Exception:  # never-raise: evidence is not a verdict
        logger.exception("mobile.case_runner._slice_evidence failed")
