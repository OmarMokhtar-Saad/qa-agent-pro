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
from typing import NamedTuple

from tools.mobile import actions as actions_mod
from tools.mobile import (
    adb,
    executor,
    ime_session,
    media,
    run_store,
    run_verdict,
    step_timing,
)
from tools.mobile.providers import composite
from tools.mobile_capture import flows as api_flows
from tools.mobile_evidence import capture, crash_detector

logger = logging.getLogger(__name__)


def note_refusal(error: object) -> str:
    """Write the one INFO ``refused:`` line for a refused script and return it."""
    return run_verdict.log_refusal(error)


MAX_ESCAPES = 3

#: How long to wait for the app to actually reach the FOREGROUND after the
#: clean launch, and how often to look. `adb.launch` returns as soon as the
#: intent is delivered, not when the app is drawn -- so the first dump raced it
#: and a case opened on whatever was in front before. On 2026-09-06 that was
#: the Settings app on its Reset options page, and the planning packet offered
#: a model "Erase all data (factory reset)" as the place to begin.
FOREGROUND_TIMEOUT_S = 8.0
FOREGROUND_POLL_S = 0.5

#: How many dumps that wait may make, WHATEVER THE CLOCK SAYS. The deadline is
#: the bound that should fire; this is the one that cannot be argued out of
#: firing. A deadline is arithmetic on a clock, and a deadline recomputed inside
#: the loop is never reached -- the wait then spins forever, which no test can
#: turn red: it HANGS, and a hung job has no attribution. A count is monotonic
#: by construction. ``FOREGROUND_TIMEOUT_S / FOREGROUND_POLL_S`` is 16, so this
#: only binds once the clock has stopped telling the truth.
MAX_FOREGROUND_POLLS = 24

#: The whole case's trace, across every submit. It is capped because a case may
#: be handed back many times -- three escapes plus eight budget stops -- and an
#: unbounded list would grow on disk and in the report.
MAX_CASE_TRACE = 240


def _foreground_refusal(seen: str, expected: str) -> str:
    return (
        "The app did not come to the foreground: after launching "
        + str(expected)[:80]
        + " the screen still belongs to "
        + str(seen or "an unknown package")[:80]
        + ". Nothing was planned against it -- a case that starts on another "
        "app's screen plans against that app. Bring "
        + str(expected)[:80]
        + " to the front and run the case again."
    )


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


def _merge_crash(prior: object, fresh: object) -> dict:
    """The case's crash record across submits: FIRST crash wins.

    A case is submitted many times -- three escapes plus eight budget stops --
    and the app died on whichever replay it died on. The terminal submit's own
    slice may be perfectly clean, so a last-write-wins record would forget the
    death that already happened. Never raises.
    """
    for candidate in (prior, fresh):
        if isinstance(candidate, dict) and candidate.get("detected"):
            return candidate
    return {}


def _crash_override(verdict: str, reason: str, crash: object) -> tuple[str, str]:
    """``(verdict, reason)`` after the app's own death is taken into account.

    THE one place a detected crash overrides a verdict, and it is deliberately a
    function of three values with no I/O: nothing else in this tree re-derives
    it, and the report and the chat read the STORED verdict and reason instead.

    * It fires ONLY on ``pass``. A ``fail``/``blocked``/``unverified`` has nothing
      to downgrade, and rewriting its reason would destroy the model's own
      finding.
    * It runs AFTER the ``VERDICT_*`` normalisation, so a verdict this server
      could not read stays ``blocked``: a crash must never promote an unreadable
      verdict into a judgement.
    * The reason it writes says the SERVER made the call, first, and quotes the
      model's own words last -- see ``crash_detector.server_reason``.
    """
    body = crash if isinstance(crash, dict) else {}
    if verdict != VERDICT_PASS or not body.get("detected"):
        return verdict, reason
    return VERDICT_FAIL, crash_detector.server_reason(body, reason)


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
#: A ``visual`` assert stop: the plan did what it said and asked for the
#: picture, so it is not an escape. Capped so a model cannot use it to loop.
REASON_VISUAL = "visual"
MAX_VISUAL_STOPS = 3
UNCHARGED_CAPS = {
    REASON_BUDGET: MAX_BUDGET_STOPS,
    REASON_SELECTOR: MAX_FREE_STOPS,
    REASON_VISUAL: MAX_VISUAL_STOPS,
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


async def _sleep(seconds: float) -> None:
    """Named so a test can replace it; a real sleep is the slowest thing here."""
    import asyncio

    await asyncio.sleep(seconds)


def merge_traces(prior: object, new: object) -> list:
    """One case's trace across every submit, renumbered as a single sequence.

    It used to be REPLACED on each checkpoint, and the 2026-09-06 live run is
    what that costs: a budget stop -- cheap and therefore common by design --
    left the report's steps table showing only the final script, so the nine
    actions that typed and sent both questions were gone from the evidence the
    report exists to give. A refused script was worse still: it contributed an
    empty list and wiped everything.

    WHICH entries the cap drops is the part that matters. The sends are at the
    start and the verdict is at the end, so the MIDDLE goes and a marker says
    how many -- dropping the head would lose exactly the evidence this is for.
    """
    merged = [item for item in list(prior or []) if isinstance(item, dict)]
    merged += [item for item in list(new or []) if isinstance(item, dict)]
    if len(merged) > MAX_CASE_TRACE:
        merged = _trim_trace_middle(merged, (MAX_CASE_TRACE - 1) // 2)
    for index, entry in enumerate(merged):
        entry["index"] = index
    return merged


def _already_dropped(merged: list) -> int:
    """EVERY action ever dropped, as recorded by earlier trim markers.

    Not just this pass's. The count came from the current list, which already
    held the previous marker as a single entry -- so each re-trim forgot what
    the last one dropped and the evidence said 61 where 182 were gone. Budget
    stops are cheap and therefore common, so a second trim is the expected path.
    """
    already = 0
    for entry in merged:
        if str(entry.get("outcome") or "") != "trimmed":
            continue
        try:
            already += int(str(entry.get("detail") or "0").split()[0])
        except (TypeError, ValueError, IndexError):
            continue
    return already


def _trim_marker(dropped: int) -> dict:
    """The single trace entry that says how many middle actions were dropped."""
    return {
        "action": {"op": "..."},
        "outcome": "trimmed",
        "detail": (
            str(dropped) + " actions in the middle of this case were dropped to "
            "bound the trace; the start and the end are kept"
        ),
        "before_screen_id": "",
        "after_screen_id": "",
        "ms": 0,
    }


def _trim_trace_middle(merged: list, keep: int) -> list:
    """Keep *keep* entries at each end and put a marker where the middle was."""
    already = _already_dropped(merged)
    merged = [entry for entry in merged if str(entry.get("outcome") or "") != "trimmed"]
    # No `+ 1`: the marker this pass strips was never an ACTION, so counting
    # it as one over-reported by exactly the number of trims. Caught by the
    # test that compares the marker against the real arithmetic instead of
    # against itself.
    dropped = already + len(merged) - (keep * 2)
    return merged[:keep] + [_trim_marker(dropped)] + merged[-keep:]


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
        refusal = _start_refusal(tc_id, ctx)
        if refusal:
            return refusal

        # Invariant 1. Unconditional, and in this order.
        stopped = await adb.force_stop(ctx.serial, ctx.package)
        if stopped.get("error"):
            return stopped
        evidence = await _begin_evidence(run_id, tc_id, ctx)
        launched = await adb.launch(ctx.serial, ctx.package)
        if launched.get("error"):
            return await _abandon(run_id, tc_id, ctx, evidence, launched)

        settle = await _wait_for_foreground(ctx)
        if settle.error:
            return await _abandon(run_id, tc_id, ctx, evidence, settle.error)
        screen = await _settled_screen(ctx, settle)
        return _write_planning(run_id, tc_id, view, screen, evidence)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.case_runner.start_case failed")
        return {"error": str(exc), "content": None}


def _start_refusal(tc_id: object, ctx: executor.Context) -> dict | None:
    """The error payload for a case that must not start, or None."""
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
    return None


async def _begin_evidence(run_id: str, tc_id: str, ctx: executor.Context) -> dict:
    """Open the per-case evidence lanes, between the force-stop and the launch.

    None of them can block the case or change a verdict: a lane that is off,
    refused or unavailable lands as a record which SAYS so, never a blank.
    """
    # App evidence (plan D5): the ring buffer is cleared BETWEEN the stop
    # and the launch, so a slice begins with the app's own start-up.
    evidence = _evidence_record(
        await capture.begin_case(ctx.serial, ctx.package, run_id, tc_id)
    )
    # The wire, beside the log (plan mobile-network-capture), started HERE so
    # the capture covers the app's own start-up.
    evidence["network"] = network_record(
        await capture.begin_network(ctx.serial, ctx.package, run_id, tc_id, 0)
    )
    # The API-capture lane's own per-case boundary (Phase 6, T6.2): a second,
    # independent dimension on the same seam.
    evidence["capture"] = capture_record(api_flows.mark_case_current(run_id, tc_id))
    return evidence


class _Settle(NamedTuple):
    """What the foreground wait saw: the last dump, its size and density."""

    error: dict | None
    dumped: dict
    sized: dict
    dpi: dict
    screen: dict
    in_front: bool


async def _observe_settle(ctx: executor.Context) -> _Settle:
    """One dump pruned WITHOUT an activity (``in_front`` is not decided here).

    THE SETTLE PRUNE passes NO activity on purpose: it answers ONE question --
    "is the app in front yet?" -- inside a loop that exists BECAUSE the answer
    is often no. `executor.resolve_activity` MEMOISES what it probes, so
    probing from in here would freeze whatever held focus mid-launch (the
    launcher, an IME, a permission dialog) onto the context. The identity is
    resolved once, after the loop. The density is a DEVICE fact like the size,
    so the accessibility audit measures 48dp against THIS device.
    """
    dumped = await executor.dump_raw(ctx.serial)
    if dumped.get("error"):
        return _Settle(dumped, dumped, {}, {}, {}, False)
    sized = await adb.display_size(ctx.serial)
    dpi = await adb.display_density(ctx.serial)
    pruned = composite.observe(
        dumped.get("content"),
        "",
        display=sized.get("content"),
        density=dpi.get("content"),
    )
    if pruned.get("error"):
        return _Settle(pruned, dumped, sized, dpi, {}, False)
    return _Settle(None, dumped, sized, dpi, pruned.get("content") or {}, False)


async def _wait_for_foreground(ctx: executor.Context) -> _Settle:
    """WAIT FOR THE APP TO BE IN FRONT; ``error`` is set when it never was.

    `adb.launch` returns when the intent is delivered, not when the app is
    drawn, so the first dump used to race it. The budget is THE WALL CLOCK,
    not the sum of the sleeps (each dump costs 15-40s on a real device). It
    reads the MODULE-LEVEL `time`, so `monkeypatch.setattr(case_runner, "time",
    ...)` is the seam that pins the deadline.
    """
    deadline = time.monotonic() + FOREGROUND_TIMEOUT_S
    polls = 0
    while True:
        seen_now = await _observe_settle(ctx)
        if seen_now.error:
            return seen_now
        seen = str(seen_now.screen.get("package") or "")
        if not seen or seen == ctx.package:
            # An empty package is a dump that named none -- accepted, not
            # waited out. TWO exits, and only ONE establishes the app is in
            # front: "the loop ended" and "the app is up" are two facts.
            return seen_now._replace(in_front=seen == ctx.package)
        # TWO bounds, and the second is not redundant: the first is only as
        # honest as the clock it reads. See MAX_FOREGROUND_POLLS.
        if time.monotonic() >= deadline or polls >= MAX_FOREGROUND_POLLS:
            refusal = {"error": _foreground_refusal(seen, ctx.package), "content": None}
            return seen_now._replace(error=refusal)
        polls += 1
        await _sleep(FOREGROUND_POLL_S)


async def _settled_screen(ctx: executor.Context, settle: _Settle) -> dict:
    """THE IDENTITY, resolved once, and ONLY when the app was in front.

    ONE producer, shared with the replay -- see `executor.resolve_activity`.
    The dump is re-pruned rather than re-fetched. Not resolving is the SAFE
    answer: the other exit is a dump that named no package, where the focused
    window may still be what the launch was moving away from, and
    `resolve_activity` memoises. A failure to resolve or re-prune leaves the
    settle screen standing -- never no screen at all.
    """
    activity = await executor.resolve_activity(ctx) if settle.in_front else ""
    if not activity:
        return settle.screen
    settled = composite.observe(
        settle.dumped.get("content"),
        activity,
        display=settle.sized.get("content"),
        density=settle.dpi.get("content"),
    )
    if settled.get("error"):
        return settle.screen
    return settled.get("content") or settle.screen


def _write_planning(
    run_id: str, tc_id: str, view: dict, screen: dict, evidence: dict
) -> dict:
    """Store the screen and the planning checkpoint; return the packet payload."""
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


#: Ops that put text on the screen through the QA keyboard.
#:
#: `clear` is here with `type`: it is an IME broadcast like the others, and a
#: CLEAR sent to a keyboard that is not active is the same silence a TYPE would
#: be. Stated as a set rather than an `op == "type"` test so a fourth text op
#: added later is caught by the same reader.
TEXT_OPS = frozenset({"type", "fill", "clear"})


def script_types(script: object) -> bool:
    """Does this script put text on screen at any point?

    Asked of the WHOLE script before the replay starts, rather than on the first
    type op as it happens: making a keyboard ready is an install and three shell
    round trips, and doing that in the middle of a replay puts a minute of
    device work between two steps that a tester reads as one action.
    """
    for action in list(getattr(script, "actions", None) or []):
        if str(getattr(action, "op", "") or "") in TEXT_OPS:
            return True
    return False


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

        prior_checkpoint = (run_store.read_case(run_id, tc_id) or {}).get("content")
        prior_checkpoint = (
            prior_checkpoint if isinstance(prior_checkpoint, dict) else {}
        )
        queued_in = list(prior_checkpoint.get("queued_actions") or [])
        if queued_in:
            decoded = actions_mod.decode_reply(raw_script)
            combined = actions_mod.combine_queued_actions(
                queued_in, list(decoded.get("actions") or [])
            )
            if not combined.get("ok"):
                note_refusal(combined.get("reason"))
                return {
                    "error": None,
                    "content": _checkpoint(
                        run_id,
                        tc_id,
                        view,
                        verdict="",
                        status=NEEDS_MODEL,
                        reason=str(combined.get("reason") or ""),
                        trace=[],
                        escapes=escapes_used(run_id, tc_id),
                        packet=None,
                        queued_actions=queued_in,
                    ),
                }
            raw_script = {"actions": combined["actions"]}

        # A folded queue was already held to both caps by the combine, so only
        # a bare submission is held to the model cap here.
        parsed = actions_mod.parse_script(
            raw_script,
            max_actions=(
                actions_mod.MAX_ACTIONS if queued_in else actions_mod.MAX_MODEL_ACTIONS
            ),
        )
        if parsed.get("error"):
            note_refusal(parsed["error"])
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
                    queued_actions=queued_in,
                ),
            }

        # THE KEYBOARD, BEFORE ANYTHING IS TYPED. `ime_session.ensure_ready` is
        # idempotent, so a run whose every case types pays for this once; a run
        # that never types never calls it and never displaces the tester's
        # keyboard. A refusal ends the case by NAME rather than replaying into a
        # keyboard that is not listening -- which is the silence this whole
        # change exists to end: ADBKeyBoard's receiver never registers on API
        # 35, so a tester's own script typed into nothing and reported success.
        if script_types(parsed["content"]):
            ready = await ime_session.ensure_ready(ctx.serial, run_id)
            if ready.get("error"):
                # R1d: NO early return. The case runs with typing over stdin; the
                # replay attaches the fallback notice (why, and the exact fix) to
                # the result. The failure is memoised per run by ensure_ready.
                ctx.typing_fallback = str(ready["error"])[:400]

        ctx.screen = screen if isinstance(screen, dict) else None
        # The pixels of this step. Started BEFORE the replay and stopped after
        # it, so the clip covers exactly what the planner's script did. Never
        # branches the run: a refused recorder returns a handle carrying the
        # reason, and the verdict path below cannot see the difference.
        # `parsed["content"]` is a validated `actions.Script` MODEL, not a list
        # -- `media.withholds` reads `.actions` off it for that reason.
        clip = (
            await media.start_clip(run_id, tc_id, parsed["content"], ctx.serial)
        ).get("content")
        fallback_before = str(getattr(ctx, "typing_fallback", "") or "")
        replayed = await executor.replay(parsed["content"], ctx)
        fallback_after = str(getattr(ctx, "typing_fallback", "") or "")
        if fallback_after and not fallback_before:
            # R1d: the keyboard was ready but would not come up inside the replay.
            # Memoise it so the next typing case does not retry the install.
            ime_session.write_fallback(run_id, fallback_after)
        if replayed.get("error"):
            # THE LEAK THIS PATH WOULD OTHERWISE BE. This returns BEFORE the
            # `finish_step` below, so the recorder started two lines up would
            # outlive the step -- and the next step's device-global
            # `pkill -INT screenrecord` would then close the WRONG recording.
            # `abandon` stops it and deletes the file without storing a record:
            # a step with no result has nowhere to show a picture or a reason.
            await media.abandon(ctx.serial, clip)
            return replayed
        step = _read_step(run_id, tc_id, replayed)
        # Outside any `if`: a step that changed nothing still started a
        # recorder, and a recorder nobody stops is a device process that
        # outlives the run. This writes the media records onto the case, and
        # `_checkpoint` rebuilds that body whole -- which is why it
        # carries `media` forward explicitly.
        await step_timing.timed(
            "evidence",
            media.finish_step(
                run_id,
                tc_id,
                ctx.serial,
                clip,
                str((step.new_screen or {}).get("screen_id") or ""),
            ),
        )
        # App evidence (plan D5): ONE slice per replay, after the trace is
        # final and before the checkpoint that carries the record forward.
        # The refused-script path never reaches here: nothing ran, so
        # there is nothing to slice.
        crash = await step_timing.timed("evidence", _slice_evidence(run_id, tc_id, ctx))
        return _outcome_checkpoint(step, view, crash)

    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.case_runner.submit_case failed")
        return {"error": str(exc), "content": None}


class _Step(NamedTuple):
    """What one replay left behind: the facts every verdict branch reads."""

    run_id: str
    tc_id: str
    result: dict
    trace: list
    used: int
    new_screen: dict


def _read_step(run_id: str, tc_id: str, replayed: dict) -> _Step:
    """Unpack a replay's result and persist the screen it ended on."""
    result = replayed.get("content") or {}
    trace = list(result.get("trace") or [])
    new_screen = result.get("screen") if isinstance(result.get("screen"), dict) else {}
    used = escapes_used(run_id, tc_id)
    if new_screen:
        run_store.write_screen(run_id, new_screen)
    return _Step(run_id, tc_id, result, trace, used, new_screen)


def _outcome_checkpoint(step: _Step, view: dict, crash: object) -> dict:
    """The checkpoint for a replayed step, by the status the executor ended on."""
    result = step.result
    status = str(result.get("status") or "")
    reason = str(result.get("reason") or "")
    if status == executor.STATUS_DONE:
        # A model-declared `pass` over a case whose app DIED is a false pass,
        # and the lane captured the proof and then never read it. The
        # override happens here and nowhere else: this is already the one
        # place a terminal verdict is normalised, so a second site would be a
        # second answer to the same question.
        verdict, done_reason = _crash_override(_terminal_verdict(result), reason, crash)
        return _step_payload(
            step,
            view,
            verdict=verdict,
            status=executor.STATUS_DONE,
            reason=done_reason,
            packet=None,
        )
    if status == executor.STATUS_NEEDS_TESTER:
        return _tester_payload(step, view)
    if status == executor.STATUS_ERROR:
        return _step_payload(
            step,
            view,
            verdict=VERDICT_BLOCKED,
            status=executor.STATUS_ERROR,
            reason=reason,
            packet=None,
        )
    return _escape_checkpoint(step, view)


def _tester_payload(step: _Step, view: dict) -> dict:
    """The needs_tester outcome: a credential request and the guard that stopped."""
    from agents import mobile_run

    result = step.result
    reason = str(result.get("reason") or "")
    packet = mobile_run.build_tester_request(
        str(result.get("field") or ""),
        reason,
        run_id=step.run_id,
        tc_id=step.tc_id,
        guard_term=str(result.get("guard_term") or ""),
    )
    return _step_payload(
        step,
        view,
        verdict="",
        status=NEEDS_TESTER,
        reason=reason,
        packet=packet,
        guard_stop={
            "term": str(result.get("guard_term") or "")[:80],
            "op": str(result.get("guard_op") or "")[:40],
            "node": str(result.get("guard_node") or "")[
                : executor.MAX_GUARD_NODE_CHARS
            ],
        },
    )


def _terminal_verdict(result: dict) -> str:
    """The executor's verdict, defaulting to pass and clamped to a known one."""
    verdict = str(result.get("verdict") or "") or VERDICT_PASS
    known = (VERDICT_PASS, VERDICT_FAIL, VERDICT_BLOCKED, VERDICT_UNVERIFIED)
    return verdict if verdict in known else VERDICT_BLOCKED


def _step_payload(step: _Step, view: dict, **fields: object) -> dict:
    """``{"error": None, "content": <checkpoint>}`` for *step*.

    ``escapes`` defaults to the step's own count; a branch that charged one
    passes the new total.
    """
    fields.setdefault("escapes", step.used)
    return {
        "error": None,
        "content": _checkpoint(
            step.run_id, step.tc_id, view, trace=step.trace, **fields
        ),
    }


def _escape_checkpoint(step: _Step, view: dict) -> dict:
    """The needs_model outcome: charge or exempt the stop, then ask or give up."""
    result, used = step.result, step.used
    uncharged = uncharged_stops(step.run_id, step.tc_id)
    reason_key = _uncharged_reason(result)
    if reason_key and uncharged[reason_key] < UNCHARGED_CAPS[reason_key]:
        uncharged[reason_key] += 1
        # ONE number per reason: see `_uncharged_reason`.
        if (
            reason_key == REASON_BUDGET
            and uncharged[reason_key] >= UNCHARGED_CAPS[reason_key]
        ):
            return _step_payload(
                step,
                view,
                verdict=VERDICT_BLOCKED,
                status=VERDICT_BLOCKED,
                reason=BUDGET_CAP_REASON,
                packet=None,
                uncharged=uncharged,
            )
    else:
        used += 1
    reason = str(result.get("reason") or "")
    if used >= MAX_ESCAPES:
        # No `uncharged=` here, and that is deliberate rather
        # than an omission. This exit is only reached by ORDINARY
        # escapes, on whose branch the counter is read straight
        # from disk and never incremented -- so passing it is
        # identical to the carry-forward default, which mutation
        # proved by deleting it and changing no result. The cap exit
        # above passes it because that is the branch that increments.
        return _step_payload(
            step,
            view,
            verdict=VERDICT_BLOCKED,
            status=VERDICT_BLOCKED,
            reason=ESCAPE_CAP_REASON + " Last stop: " + reason,
            escapes=used,
            packet=None,
            uncharged=uncharged,
        )
    return _ask_model_payload(step, view, used, uncharged, reason_key)


def _ask_model_payload(
    step: _Step, view: dict, used: int, uncharged: dict, reason_key: str
) -> dict:
    """Hand the model the escape-hatch job for a stop that is still allowed."""
    from agents import mobile_run

    result = step.result
    reason = str(result.get("reason") or "")
    packet = mobile_run.build_escape_job(
        view,
        step.new_screen,
        step.trace,
        run_id=step.run_id,
        tc_id=step.tc_id,
        escapes=used,
        reason=reason,
    )
    return _step_payload(
        step,
        view,
        verdict="",
        status=NEEDS_MODEL,
        reason=reason,
        escapes=used,
        packet=packet,
        uncharged=uncharged,
        # A fresh budget stop's real remainder, and ONLY that -- a
        # selector-stale or ordinary escape stop clears the queue by
        # passing None, same as every other checkpoint that omits
        # this keyword.
        queued_actions=(
            result.get("queued_actions") if reason_key == REASON_BUDGET else None
        ),
    )


def _uncharged_reason(result: dict) -> str:
    """Which exempt-from-escape reason a needs_model stop carries, else ``""``.

    TWO reasons a stop is not charged as an escape, ONE mechanism.

    A BUDGET STOP IS NOT AN ESCAPE. The executor stops a replay that would
    outlive the client's tool timeout; the script was legal, the actions
    that ran are in the trace, and the model is being asked to continue
    rather than to re-plan. Charging one of three escapes for obeying our
    own bound turns a correct case into `blocked`.

    A STALE-SELECTOR STOP IS NOT AN ESCAPE EITHER, when nothing touched
    the device first: `perception` mints the id, and the app moving an
    element is not the tester's case failing to make progress.

    The caps differ because the evidence differs -- a long case
    legitimately budget-stops many times, a plan whose own selector went
    stale should not get many retries -- so they are held per reason in
    UNCHARGED_CAPS rather than as one number. One counter, one disclosure,
    one carry-forward; two bounds, neither laxer than it was alone.

    ONE number per reason. The cap check used to read MAX_BUDGET_STOPS while
    the exemption read UNCHARGED_CAPS, so for the budget reason the dict
    entry was INERT -- measured 2026-09-04: raising it to 99 changed no
    behaviour and passed all 1410 mobile tests, while raising the constant
    was caught by the peer's own static guard. A redundant bound that reads
    as authoritative is how a later retune of the unified counter silently
    does nothing.
    """
    if result.get("budget_stop"):
        return REASON_BUDGET
    if bool(result.get("selector_stale")) and not result.get("actuated"):
        return REASON_SELECTOR
    if result.get("visual_check"):
        return REASON_VISUAL
    return ""


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
    guard_stop: object = None,
    queued_actions: object = None,
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
        # ACCUMULATED, not replaced -- see `merge_traces`.
        "trace": merge_traces(prior.get("trace"), trace),
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
        # NOT carried forward from `prior`, unlike `uncharged_stops` above: a
        # guard stop this call did not just produce is not THIS turn's stop,
        # and carrying an old one forward would let a later turn's confirm
        # match a control nobody stopped on THIS submission.
        "guard_stop": dict(guard_stop) if isinstance(guard_stop, dict) else {},
        # CLEARED BY DEFAULT (`queued_actions=None`), unlike `uncharged_stops`
        # above: a queue not explicitly carried forward is queue the model has
        # either consumed or that no longer applies -- see call sites for the
        # three cases that pass it explicitly.
        "queued_actions": (
            list(queued_actions) if isinstance(queued_actions, list) else []
        ),
        "started": prior.get("started") or now,
        "updated": now,
        "evidence": _evidence_record(prior.get("evidence")),
        # CARRY-FORWARD, same reason as `started` and `evidence`: this body
        # REPLACES the case whole, and `media.remember` wrote its records onto
        # the case moments ago, on this same submit. Without this line the
        # clips and frames are written to disk and then erased from their only
        # index before any consumer sees one -- silently, because the report
        # simply finds no media and renders the old wireframe.
        "media": [
            item for item in list(prior.get("media") or []) if isinstance(item, dict)
        ],
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
        (REASON_VISUAL, ""),
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


async def _abandon(
    run_id: str, tc_id: str, ctx: executor.Context, evidence: dict, result: dict
) -> dict:
    """Stop the case's network capture and hand *result* back UNCHANGED.

    The four early returns of ``start_case`` after the capture has started all
    pass through here, so a refused case cannot leave the console recording and
    a packet dump on disk. The abort can never change what the caller is told:
    evidence is not a verdict, and a failure to stop is logged, not raised.
    """
    try:
        await capture.abort_network(
            ctx.serial, run_id, tc_id, (evidence or {}).get("network")
        )
        # Stops the API-capture lane the same way: an abandoned case must
        # not go on attributing flows to a case that never finished.
        api_flows.mark_case_finished(run_id)
    except Exception:  # never-raise: evidence is not a verdict
        logger.exception("mobile.case_runner._abandon failed")
    return result


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
    crash = body.get("crash")
    return {
        "profile": body.get("profile"),
        "clock_offset_ms": body.get("clock_offset_ms"),
        "pid": body.get("pid"),
        "skipped": body.get("skipped") or holder.get("error") or None,
        "slices": slices,
        "slices_written": list(written) if isinstance(written, list) else [],
        # Present whatever is on disk, so the report never reads a missing key
        # and a record written before this feature reads as "no crash" rather
        # than raising inside a checkpoint.
        "crash": crash if isinstance(crash, dict) and crash.get("detected") else {},
        # Carried forward the same way the crash is, and normalised by its own
        # accessor: a checkpoint written before this feature has no such key,
        # and the report must read a shape rather than a KeyError.
        "network": network_record(body.get("network")),
        # Carried forward the same way "network" is: a checkpoint written
        # before this feature has no such key, and the report must read a
        # shape rather than a KeyError.
        "capture": capture_record(body.get("capture")),
    }


def network_record(source: object) -> dict:
    """The case network record, normalised from ``capture.begin_network`` or
    ``finish_network``, OR from a record already on disk.

    EVERY KEY PRESENT, including on a checkpoint written before this feature
    existed, where each one is its empty value. The report reads these keys; a
    missing one is a KeyError inside a page, which is not a gap the page can
    state. Never raises: a corrupt count on disk is a zero here, not an
    exception inside a checkpoint."""
    holder = source if isinstance(source, dict) else {}
    body = holder.get("content") if "content" in holder else holder
    body = body if isinstance(body, dict) else {}

    def count(value):
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError, OverflowError):
            return 0

    uid = body.get("uid")
    started_ms = body.get("started_ms")
    rows = body.get("rows")
    return {
        "skipped": body.get("skipped") or holder.get("error") or None,
        "stage": str(body.get("stage") or ""),
        "started": bool(body.get("started")),
        "file": str(body.get("file") or ""),
        "uid": uid if isinstance(uid, int) else None,
        "rows": [row for row in (rows or ()) if isinstance(row, dict)],
        "packets": count(body.get("packets")),
        "samples": count(body.get("samples")),
        "truncated": bool(body.get("truncated")),
        "note": str(body.get("note") or ""),
        "path": str(body.get("path") or ""),
        "started_ms": started_ms if isinstance(started_ms, int) else None,
    }


def capture_record(source: object) -> dict:
    """The case's API-capture flow record, normalised from
    ``api_flows.mark_case_current`` / ``finish_case``, OR from a record
    already on disk. EVERY KEY PRESENT; never raises. Beside
    ``network_record`` -- a second, independent dimension on the same
    per-case evidence seam: this lane sees inside TLS, the pcap lane does
    not."""
    holder = source if isinstance(source, dict) else {}
    body = holder.get("content") if "content" in holder else holder
    body = body if isinstance(body, dict) else {}
    try:
        flow_count = max(0, int(body.get("flow_count") or 0))
    except (TypeError, ValueError, OverflowError):
        flow_count = 0
    flows = body.get("flows")
    return {
        "skipped": body.get("skipped") or holder.get("error") or None,
        "flow_count": flow_count,
        "flows": [row for row in (flows or ()) if isinstance(row, dict)],
        "truncated_by_count": bool(body.get("truncated_by_count")),
    }


async def _slice_evidence(run_id: str, tc_id: str, ctx: executor.Context) -> dict:
    """Take this replay's logcat slice, record it, and return the case's crash.

    Reads the record ``start_case`` left, asks ``capture`` for the slice --
    which redacts the tester's typed values BEFORE anything reaches disk (plan
    D4) and judges the scrubbed text for a fatal event -- and writes the updated
    record back so the checkpoint that follows carries it forward. A skipped or
    failed slice is stored as such.

    **The STORING can never change or block a verdict**, which is why every
    exception still ends here. The returned value is a separate thing: the crash
    record for the CASE (first crash wins, ``_merge_crash``), which the caller
    hands to ``_crash_override`` one branch later. On any failure at all -- a
    detector that raised, a slice that was never taken, a store that would not
    read -- this returns ``{}``, i.e. "no crash proven", so an evidence fault
    leaves the model's verdict exactly as it was.
    """
    try:
        prior = (run_store.read_case(run_id, tc_id) or {}).get("content")
        prior = prior if isinstance(prior, dict) else {}
        evidence = _evidence_record(prior.get("evidence"))
        if evidence.get("skipped"):
            return _merge_crash(evidence.get("crash"), None)
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
        this_crash = content.get("crash")
        this_crash = this_crash if isinstance(this_crash, dict) else {}
        evidence["slices_written"].append(
            {
                "index": evidence["slices"] - 1,
                "path": str(content.get("path") or ""),
                "lines": content.get("lines"),
                "truncated": bool(content.get("truncated")),
                "skipped": content.get("skipped")
                or (sliced.get("error") if isinstance(sliced, dict) else None),
                "crash": this_crash,
            }
        )
        evidence["crash"] = _merge_crash(evidence.get("crash"), this_crash)
        # The wire is stopped, parsed and attributed at the SAME checkpoint the
        # log slice is taken, and spliced in beside it. Nothing branches on the
        # result: an evidence fault can no more change a verdict here than a
        # failed slice can, which is why this sits after the crash merge and
        # before the write rather than anywhere a return could skip it.
        evidence["network"] = network_record(
            await capture.finish_network(
                ctx.serial,
                ctx.package,
                run_id,
                tc_id,
                max(0, evidence["slices"] - 1),
                evidence.get("network"),
            )
        )
        # Same checkpoint, same discipline, the API-capture lane's own
        # dimension: never branched on, an evidence fault here can no more
        # change a verdict than a failed log slice can.
        evidence["capture"] = capture_record(
            api_flows.finish_case(run_id, tc_id, tester_inputs=ctx.tester_inputs)
        )
        # NEVER write a document read before an await. A checkpoint may have
        # landed while the device was being read; the fresh copy carries its
        # verdict and trace, and only the evidence record is spliced in.
        fresh = (run_store.read_case(run_id, tc_id) or {}).get("content")
        fresh = fresh if isinstance(fresh, dict) else prior
        fresh["evidence"] = evidence
        run_store.write_case(run_id, tc_id, fresh)
        return _merge_crash(evidence.get("crash"), None)
    except Exception:  # never-raise: evidence is not a verdict
        logger.exception("mobile.case_runner._slice_evidence failed")
        return {}
