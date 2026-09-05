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

#: How many times ONE case may be stopped by the submit budget before it is
#: ended anyway. A budget stop is not an escape -- the script was legal and the
#: model is being asked to continue rather than to re-plan -- but exempting it
#: outright removed the ONLY bound on the model/server loop for a case: nothing
#: else caps submits, so a model re-sending the same over-budget script never
#: terminates and the run never finishes. Cheap, then, but not free.
MAX_BUDGET_STOPS = 8

BUDGET_CAP_REASON = (
    "This case was stopped by the per-submit time budget "
    + str(MAX_BUDGET_STOPS)
    + " times without finishing, so it is recorded as blocked rather than "
    "continued again. Each script it was given needed more device time than "
    "one submit allows; the trace shows how far it got."
)

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

#: Boomerangs that may be spent WITHOUT charging an escape, per case, when the
#: stop was caused by one of our own selectors going stale and nothing had
#: touched the device yet.
#:
#: The precedent is this module's own, one branch below: "A refused script is
#: NOT an escape: nothing was replayed, so the planner gets the same screen
#: back and one of its escapes is not spent on our own validation." A stale id
#: is the same category of stop -- ``perception`` mints the id, and the app
#: moving the element is not the tester's case failing.
#:
#: It is CAPPED rather than free, because an uncapped uncharged stop is a loop
#: with the tester's tokens in it. After this many, a stale-id stop is charged
#: like any other boomerang, so ``MAX_ESCAPES`` still terminates the case.
#:
#: It does NOT cover the post-``type`` case, and that is measured rather than
#: assumed: a ``type`` actuates the device, so a stale-id stop after one has
#: ``actuated=True`` and IS charged. The answer there is the packet's own
#: instruction to target by ``rid``/text after a ``type`` -- see
#: ``tests/mobile/test_mobile_escape_budget.py``, which measures both.
MAX_FREE_STOPS = 2

#: The two reasons a stop is not charged, and the cap on each. ONE counter with
#: a bound PER REASON: a single bound cannot be both "not laxer than 2 for a
#: stale selector" and "not stricter than 8 for a budget stop", and each number
#: has its own evidence behind it.
REASON_BUDGET = "budget"
REASON_SELECTOR = "selector"
UNCHARGED_CAPS = {
    REASON_BUDGET: MAX_BUDGET_STOPS,
    REASON_SELECTOR: MAX_FREE_STOPS,
}


def uncharged_stops(run_id: str, tc_id: str) -> dict:
    """Every uncharged stop this case has spent, by reason, read from disk.

    On disk for the same reason ``escapes`` is: the MCP server restarts on every
    code and ``.env`` edit, and an in-memory counter would mean "free forever,
    one restart at a time".

    Reads the merged ``uncharged_stops`` dict, and falls back to the two flat
    keys a checkpoint written by either branch before the merge would carry --
    so a run started on either side is still counted correctly.
    """
    body = (run_store.read_case(run_id, tc_id) or {}).get("content")
    body = body if isinstance(body, dict) else {}
    return _prior_uncharged(body)


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


def budget_stops_used(run_id: str, tc_id: str) -> int:
    """How many times the submit budget has already ended this case, from disk.

    On the checkpoint for the same reason the escape count is: the MCP server
    restarts on every code and `.env` edit, and a counter held in memory would
    mean "loop forever, one restart at a time".
    """
    body = (run_store.read_case(run_id, tc_id) or {}).get("content")
    if not isinstance(body, dict):
        return 0
    try:
        return uncharged_stops(run_id, tc_id)[REASON_BUDGET]
    except (TypeError, ValueError):
        return 0


def escapes_used(run_id: str, tc_id: str) -> int:
    """How many times this case has already boomeranged, read from disk."""
    body = (run_store.read_case(run_id, tc_id) or {}).get("content")
    if not isinstance(body, dict):
        return 0
    try:
        return max(0, int(body.get("escapes") or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def free_stops_used(run_id: str, tc_id: str) -> int:
    """Uncharged stale-selector stops this case has already spent, from disk.

    On disk for the same reason ``escapes`` is: the MCP server restarts on every
    code and ``.env`` edit, and an in-memory counter would mean "free forever,
    one restart at a time".
    """
    body = (run_store.read_case(run_id, tc_id) or {}).get("content")
    if not isinstance(body, dict):
        return 0
    try:
        return uncharged_stops(run_id, tc_id)[REASON_SELECTOR]
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
        #
        # TWO reasons a stop is not charged as an escape, ONE mechanism.
        #
        # A BUDGET STOP IS NOT AN ESCAPE. The executor stops a replay that would
        # outlive the client's tool timeout; the script was legal, the actions
        # that ran are in the trace, and the model is being asked to continue
        # rather than to re-plan. Charging one of three escapes for obeying our
        # own bound turns a correct case into `blocked`.
        #
        # A STALE-SELECTOR STOP IS NOT AN ESCAPE EITHER, when nothing touched
        # the device first: `perception` mints the id, and the app moving an
        # element is not the tester's case failing to make progress.
        #
        # The caps differ because the evidence differs -- a long case
        # legitimately budget-stops many times, a plan whose own selector went
        # stale should not get many retries -- so they are held per reason in
        # UNCHARGED_CAPS rather than as one number. One counter, one disclosure,
        # one carry-forward; two bounds, neither laxer than it was alone.
        uncharged = uncharged_stops(run_id, tc_id)
        reason_key = ""
        if result.get("budget_stop"):
            reason_key = REASON_BUDGET
        elif bool(result.get("selector_stale")) and not result.get("actuated"):
            reason_key = REASON_SELECTOR
        if reason_key and uncharged[reason_key] < UNCHARGED_CAPS[reason_key]:
            uncharged[reason_key] += 1
            # ONE number per reason. This used to read MAX_BUDGET_STOPS while
            # the exemption above read UNCHARGED_CAPS, so for the budget reason
            # the dict entry was INERT -- measured 2026-09-04: raising it to 99
            # changed no behaviour and passed all 1410 mobile tests, while
            # raising the constant was caught by the peer's own static guard. A
            # redundant bound that reads as authoritative is how a later retune
            # of the unified counter silently does nothing.
            if (
                reason_key == REASON_BUDGET
                and uncharged[reason_key] >= UNCHARGED_CAPS[reason_key]
            ):
                return {
                    "error": None,
                    "content": _checkpoint(
                        run_id,
                        tc_id,
                        view,
                        verdict=VERDICT_BLOCKED,
                        status=VERDICT_BLOCKED,
                        reason=BUDGET_CAP_REASON,
                        trace=trace,
                        escapes=used,
                        packet=None,
                        uncharged=uncharged,
                    ),
                }
        else:
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
                    # No `uncharged=` here, and that is deliberate rather
                    # than an omission. This exit is only reached by ORDINARY
                    # escapes, on whose branch the counter is read straight
                    # from disk and never incremented -- so passing it is
                    # identical
                    # to the carry-forward default, which mutation proved by
                    # deleting it and changing no result. The cap exit above
                    # passes it because that is the branch that increments.
                    reason=ESCAPE_CAP_REASON
                    + " Last stop: "
                    + str(result.get("reason") or ""),
                    trace=trace,
                    escapes=used,
                    packet=None,
                    uncharged=uncharged,
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
                uncharged=uncharged,
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
    uncharged: object = None,
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
        # ONE carry-forward for both reasons. Every terminal checkpoint
        # replaces this body whole, so a count this call did not compute must
        # survive -- and `_prior_uncharged` guards the read on BOTH sides,
        # because a corrupt count on disk (a hand-edited checkpoint, a file from
        # another build) used to raise inside every later checkpoint for that
        # case, which `submit_case`'s own except then reported as a handled
        # error with no verdict written.
        "uncharged_stops": (
            dict(uncharged) if isinstance(uncharged, dict) else _prior_uncharged(prior)
        ),
        "started": prior.get("started") or now,
        "updated": now,
        "evidence": _evidence_record(prior.get("evidence")),
    }
    written = run_store.write_case(run_id, tc_id, body)
    payload = dict(body)
    payload["packet"] = packet
    payload["checkpoint_error"] = written.get("error")
    return payload


def _prior_uncharged(prior: object) -> dict:
    """A checkpoint's uncharged-stop counts, by reason, never raising.

    Guarded because the carry-forward used to do a bare ``int()`` on whatever
    was on disk, so a corrupt count broke every later checkpoint for that case.
    Reads the merged key first, then the two pre-merge flat keys -- and reads
    the legacy one when the merged value is UNREADABLE as well as when it is
    missing. ``merged.get(reason, body.get(legacy))`` only did the second, so a
    corrupt merged value discarded a good legacy one: measured,
    ``{"uncharged_stops": {"budget": "x"}, "budget_stops": 3}`` returned
    ``budget: 0``, which hands that one case a full cap of extra uncharged
    stops. Not a loop -- the next checkpoint writes a clean merged dict -- and
    not laxer than the pre-merge behaviour, but wrong.
    """
    body = prior if isinstance(prior, dict) else {}
    merged = body.get("uncharged_stops")
    merged = merged if isinstance(merged, dict) else {}
    out = {}
    for reason, legacy in (
        (REASON_BUDGET, "budget_stops"),
        (REASON_SELECTOR, "free_stops"),
    ):
        value = _uncharged_count(merged.get(reason))
        if value is None:
            value = _uncharged_count(body.get(legacy))
        out[reason] = 0 if value is None else value
    return out


def _uncharged_count(raw: object) -> int | None:
    """One stored count, coerced, or ``None`` when it cannot be read.

    ``None`` rather than ``0`` for the failure, because the caller has to tell
    "unreadable, try the other key" from "the stored count really is zero" --
    conflating the two is the defect this helper exists to remove.

    ``""`` counts as unreadable: a blank string on disk is corruption, not a
    zero. ``OverflowError`` is caught alongside the other two because
    ``int(float("inf"))`` raises it (``int(float("nan"))`` raises ValueError),
    and this function's whole contract is that it never raises into a
    checkpoint.
    """
    try:
        if raw is None or raw == "":
            return None
        return max(0, int(raw))
    except (TypeError, ValueError, OverflowError):
        return None


def _evidence_record(source: object) -> dict:
    """The case's evidence record, normalised from ``capture.begin_case``'s
    reply OR from a record already on disk. Every key present, so the report
    never reads a missing one, and the slice counter is an int."""
    holder = source if isinstance(source, dict) else {}
    body = holder.get("content") if "content" in holder else holder
    body = body if isinstance(body, dict) else {}
    try:
        slices = max(0, int(body.get("slices") or 0))
    except (TypeError, ValueError, OverflowError):
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
