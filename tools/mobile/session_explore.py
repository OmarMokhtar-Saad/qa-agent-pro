"""Mobile session: explore-lane turn handling. Cut verbatim from tools/mobile/session.py (P4.4)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from tools.mobile import case_runner, run_store
from tools.mobile.replay.context import Context
from tools.mobile.replay.guard import MAX_GUARD_NODE_CHARS, NOTHING_MOVED_PREFIX
from tools.mobile.session_core import _CASE_STORE_ERRORS
from tools.mobile_evidence import capture

logger = logging.getLogger("tools.mobile.session")


#: What an exploratory turn is checkpointed as. An explore run used to write NO
#: case record at all: ``_submit_explore`` replayed the turn, folded the reply
#: and returned, while ``tools/mobile/report.py`` is case-driven from
#: ``cases/TC-*.json`` -- so a real four-turn run
#: (``mrun-20260904-194622-bd5142``) produced a 147 KB report reading "0 cases
#: checkpointed" and "No case has been checkpointed yet". A turn IS the unit of
#: work in this lane, so it is checkpointed as one, through the same
#: ``run_store`` path the suite lane uses. That is why the existing report
#: machinery draws it with no change to what a "case" means on disk.
EXPLORE_MODULE = "Exploration"
EXPLORE_TITLE_PREFIX = "Exploratory turn "


def explore_turn_tc_id(turn: object) -> str:
    """The synthetic, STABLE case id for one exploratory turn.

    Stable is the load-bearing word: the same turn submitted twice (a refused
    script, a resumed chat) must reuse its id, or the report grows a duplicate
    card per retry. The turn number is the only identifier a turn has that
    survives an MCP restart, and ``run_store.valid_tc_id`` already accepts
    exactly this shape -- so no store contract moves for this fix.
    """
    try:
        number = int(turn or 0)
    except (TypeError, ValueError, OverflowError):
        number = 0
    return "TC-%03d" % max(1, min(number, 999999))


def explore_turn_number(state: object) -> int:
    """THE turn number an exploratory state is on. One coercion, one place.

    The checkpoint needs the NUMBER (it titles the card with it) and the
    recorder needs the ID; both must agree, so the coercion that turns a junk
    field into a usable number lives here and nowhere else.
    """
    body = state if isinstance(state, dict) else {}
    try:
        return max(1, int(body.get("turn") or 1))
    except (TypeError, ValueError, OverflowError):
        return 1


def explore_turn_case_id(state: object) -> str:
    """THE case id one exploratory turn writes, derived from its STATE.

    ONE producer, because two callers need the same answer at two moments:
    ``_checkpoint_explore_turn`` WRITES the turn's case under this id, and the
    recorder around the replay must file its clip onto that same case -- and it
    needs the id BEFORE the checkpoint exists. Deriving the number twice is how
    a clip lands on a case id nothing else uses, which is silent: the report
    finds no media on the case it renders and draws the old wireframe.
    """
    return explore_turn_tc_id(explore_turn_number(state))


def _latest_finding(state: object) -> str:
    """The finding recorded for the CURRENT turn, or ``""``.

    Matched on the turn number rather than taken as the last entry: findings
    are appended only when a turn reports one, so "the last note" would make a
    silent turn inherit the previous turn's finding and report evidence that
    turn never produced.
    """
    body = state if isinstance(state, dict) else {}
    try:
        turn = int(body.get("turn") or 0)
    except (TypeError, ValueError, OverflowError):
        return ""
    notes = [f for f in list(body.get("findings") or []) if isinstance(f, dict)]
    for entry in reversed(notes):
        try:
            if int(entry.get("turn") or 0) == turn:
                return str(entry.get("note") or "")
        except (TypeError, ValueError, OverflowError):
            continue
    return ""


def _explore_prior_verified(run_id: str) -> bool:
    """True when an EARLIER turn of this run recorded an ``assert_pass``.

    The explore lane checkpoints one turn as one case, so the turn that calls
    ``done`` has a trace of its own that can never carry the check an earlier
    turn made. The unit that earns a verdict here is the RUN -- the goal -- so
    the evidence is read run-wide and handed to the executor as an explicit
    input (``executor.Context.prior_verified``). The executor still knows
    nothing about runs, and the scripted lane is untouched because that field
    defaults to False.

    The OUTCOME is what counts, not the presence of an assert: a run whose only
    check FAILED has verified nothing.

    Never raises: an unreadable case store is "no prior evidence".
    """
    try:
        listed = run_store.list_cases(run_id) or {}
        for body in listed.get("content") or []:
            if not isinstance(body, dict):
                continue
            for item in list(body.get("trace") or []):
                if (
                    isinstance(item, dict)
                    and str(item.get("outcome") or "") == "assert_pass"
                ):
                    return True
    except _CASE_STORE_ERRORS:  # pragma: no cover - defensive
        logger.warning("mobile.session: could not read this run's prior evidence")
    return False


def _explore_prior_turn_blocked(run_id: str) -> bool:
    """True when the LATEST checkpointed turn failed to type or moved nothing.

    ONE turn, not run-wide like :func:`_explore_prior_verified`: a failure
    several turns back must not block every later pass once the app moved on.
    "Latest" is by the turn number in ``tc_id``, not list position. Counted: a
    reason carrying :data:`executor.NOTHING_MOVED_PREFIX`, or a ``type``/
    ``clear`` whose outcome was ``device_error`` or ``refused``.

    Never raises: an unreadable case store is "not blocked".
    """
    try:
        listed = run_store.list_cases(run_id) or {}
        latest: dict | None = None
        latest_turn = -1
        for body in listed.get("content") or []:
            if not isinstance(body, dict):
                continue
            digits = "".join(ch for ch in str(body.get("tc_id") or "") if ch.isdigit())
            turn = int(digits) if digits else -1
            if turn >= latest_turn:
                latest, latest_turn = body, turn
        if latest is None:
            return False
        if NOTHING_MOVED_PREFIX in str(latest.get("reason") or ""):
            return True
        for item in list(latest.get("trace") or []):
            if not isinstance(item, dict):
                continue
            action = item.get("action") if isinstance(item.get("action"), dict) else {}
            if str(action.get("op") or "") in ("type", "fill", "clear") and str(
                item.get("outcome") or ""
            ) in ("device_error", "refused"):
                return True
    except _CASE_STORE_ERRORS:  # pragma: no cover - defensive
        logger.warning("mobile.session: could not read this run's previous turn")
    return False


def _prior_guard_stop_explore(run_id: str, tc_id: str) -> dict:
    """The explore lane's own guard-stop lookup: an EXACT match on ``tc_id``
    first (the common case, a resubmission of the SAME turn), and only when
    that misses, the highest-turn checkpointed case's own ``guard_stop`` --
    verbatim, so a confirm still spends against exactly what that turn
    recorded (``{}`` if that turn recorded none, which keeps the one-shot
    confirm inert rather than manufacturing a stop for it to consume).

    This exists because the explore lane's turn counter
    (``explore_runner.next_packet``) bumps ``state["turn"]`` on EVERY call,
    not only across a resubmission of the same turn -- so a confirm
    submitted after the scheduler already advanced past this turn names a
    ``tc_id`` no case was ever checkpointed under, and the exact-match lookup
    alone returns ``{}`` and the guard fires again on a turn that already
    confirmed. The scripted lane never reaches this: it always has an exact
    ``tc_id`` match once a case exists, so :func:`_prior_guard_stop` alone
    still serves it directly. FAILS CLOSED the same way its sibling does:
    any read that raises, or no checkpointed case at all, is untracked --
    ``{}``.
    """
    exact = _prior_guard_stop(run_id, tc_id)
    if exact:
        return exact
    try:
        listed = run_store.list_cases(run_id) or {}
        rows = [b for b in (listed.get("content") or []) if isinstance(b, dict)]
        if not rows:
            return {}
        # (length, text): TC-\d{3,6} sorts numerically, so TC-1000 > TC-999.
        latest = max(
            rows,
            key=lambda b: (len(str(b.get("tc_id") or "")), str(b.get("tc_id") or "")),
        )
        stop = latest.get("guard_stop")
        return dict(stop) if isinstance(stop, dict) else {}
    except _CASE_STORE_ERRORS:  # pragma: no cover - defensive
        logger.warning(
            "mobile.session: could not read the latest explore turn's guard stop"
        )
        return {}


def _prior_guard_stop(run_id: str, tc_id: str) -> dict:
    """THIS case's own last recorded guard-stop fingerprint, or ``{}``.

    Scoped to the ONE case being submitted -- an exact match on ``tc_id`` --
    not a run-wide "latest by tc_id digit" scan: that scan's "latest" case
    can be a DIFFERENT tc_id than the one this confirm names, which would let
    a stop recorded against one case authorise a confirm submitted for
    another. The scripted lane passes its own ``tc_id`` and stops here. The
    explore lane no longer calls this function directly -- its turn counter
    advances on EVERY packet, not only across a resubmission, so an exact
    match alone misses a confirm submitted after the scheduler moved on; see
    :func:`_prior_guard_stop_explore`, which tries this exact match FIRST
    and only then falls back.

    FAILS CLOSED: any read that raises, or a matching case that recorded no
    stop, is untracked -- ``{}`` -- rather than treated as leniently as
    :func:`_explore_prior_turn_blocked` treats its own question. That
    sibling fails OPEN because its wrong "no" only costs one extra retry;
    this one's wrong "no" would let a mismatched confirm through, which is
    the defect this function exists to close.
    """
    try:
        listed = run_store.list_cases(run_id) or {}
        for body in listed.get("content") or []:
            if isinstance(body, dict) and str(body.get("tc_id") or "") == str(tc_id):
                stop = body.get("guard_stop")
                return dict(stop) if isinstance(stop, dict) else {}
    except _CASE_STORE_ERRORS:  # pragma: no cover - defensive
        logger.warning("mobile.session: could not read this case's last guard stop")
    return {}


def _explore_planned_case(tc_id: str, state: object, finding: str = "") -> dict:
    """The turn as a MINIMAL valid ``TestCase`` dump, for ``manifest["cases"]``.

    Valid rather than ad-hoc because :func:`load_case` and
    :func:`load_run_cases` rehydrate every entry of that list through
    ``TestCase(**body)``: a shape that failed validation would be dropped with
    a warning, and the re-run-failures source would silently lose the run. It
    is also what lets the report's ``_planned_case`` print what the turn was
    FOR.
    """
    body = state if isinstance(state, dict) else {}
    goal = " ".join(str(body.get("goal") or "").split())[:400] or "not recorded"
    try:
        turn = max(1, int(body.get("turn") or 1))
    except (TypeError, ValueError, OverflowError):
        turn = 1
    note = " ".join(str(finding or "").split())[:400]
    return {
        "tc_id": tc_id,
        "module": EXPLORE_MODULE,
        "title": (EXPLORE_TITLE_PREFIX + str(turn) + " \u2014 " + goal)[:250],
        "priority": "Medium",
        "type": "Exploratory",
        "preconditions": "The app was open at the previous turn's screen.",
        "steps": [
            {
                "step_number": 1,
                "action": "Explore towards the goal: " + goal,
                "expected_result": (
                    "Recorded finding: " + note
                    if note
                    else (
                        "An exploratory turn has no expected result; it records "
                        "what was found."
                    )
                ),
            }
        ],
        "automation_status": "Manual",
    }


@dataclass(frozen=True)
class _TurnNotes:
    """What the caller adds to an explore turn's checkpoint beyond its outcome."""

    finding: str = ""
    verdict: str = ""
    network: object = None


def _checkpoint_explore_turn(
    run_id: str,
    state: object,
    outcome: object,
    notes: _TurnNotes | None = None,
) -> dict:
    """Write ONE exploratory turn as a case-shaped record. Never raises.

    Best-effort exactly like ``run_store.write_screen``: a lost checkpoint may
    never lose a turn's replay, so nothing here can return an error into the
    submit path. Returns the body it wrote (or tried to), because the caller
    reports the same id and title back to the tester.

    The record carries a STATUS and, on every turn but one, no verdict. An
    exploratory turn has no expected result, so it has none to earn, and
    ``report._verdict_of`` shows the status instead -- never inventing a pass.
    The exception is a turn whose ``done()`` judged the goal: the unit that
    earns a verdict in this lane is the RUN, and the caller passes that verdict
    in so the case-driven report has one to render, in the same field the suite
    lane uses.
    """
    notes = notes or _TurnNotes()
    finding, verdict, network = notes.finding, notes.verdict, notes.network
    body = state if isinstance(state, dict) else {}
    result = outcome if isinstance(outcome, dict) else {}
    turn = explore_turn_number(body)
    tc_id = explore_turn_tc_id(turn)
    goal = " ".join(str(body.get("goal") or "").split())[:200] or "no goal recorded"
    now = time.time()
    began = _turn_began(body, now)
    written = {
        "tc_id": tc_id,
        "title": (EXPLORE_TITLE_PREFIX + str(turn) + " \u2014 " + goal)[:250],
        **_explore_result_fields(result, verdict),
        "escapes": 0,
        "guard_stop": _explore_guard_stop(result),
        "finding": " ".join(str(finding or "").split())[:600],
        "started": min(began, now),
        "updated": now,
        "evidence": _explore_turn_evidence(network),
    }
    try:
        run_store.write_case(run_id, tc_id, written)
    except Exception:  # pragma: no cover - defensive
        logger.warning("mobile.session: could not checkpoint an explore turn")
    return written


def _turn_began(body: dict, now: float) -> float:
    """When this turn's packet was built, or *now* when the record has no start."""
    # The window this case's evidence is joined on is ``[started, updated]``.
    # ``started`` is when the PACKET was built, not when this record is written,
    # or the window is the pad around a single instant at the end of the turn
    # and everything the app did during it falls outside.
    began = body.get("turn_started")
    if not isinstance(began, (int, float)) or isinstance(began, bool) or began <= 0:
        return now
    return began


def _explore_result_fields(result: dict, verdict: str) -> dict:
    """The verdict, status, reason and trace of one turn's replay result."""
    return {
        "verdict": str(verdict or ""),
        "status": str(result.get("status") or ""),
        "reason": str(result.get("reason") or "")[:1200],
        "trace": list(result.get("trace") or []),
    }


def _explore_guard_stop(result: dict) -> dict:
    """The guard fingerprint a later explore-lane confirm binds to."""
    # Same fingerprint shape `case_runner._checkpoint` writes, from the
    # SAME per-submit `result` this turn's own replay returned -- so an
    # explore-lane confirm binds to the op THIS run's last turn stopped
    # on, exactly like the scripted lane's `_consume_confirm`. Not
    # carried forward from a prior turn's checkpoint, for the same
    # reason `case_runner._checkpoint` does not carry it forward either.
    return {
        "term": str(result.get("guard_term") or "")[:80],
        "op": str(result.get("guard_op") or "")[:40],
        "node": str(result.get("guard_node") or "")[:MAX_GUARD_NODE_CHARS],
    }


def _explore_turn_evidence(network: object) -> dict:
    """An evidence record shaped like ``case_runner._evidence_record``'s output."""
    # Shaped like `case_runner._evidence_record`'s output so the report's
    # evidence join reads a record rather than a missing key.
    return {
        "profile": None,
        "clock_offset_ms": None,
        "pid": None,
        "skipped": "an exploratory turn takes no app-log slice",
        "slices": 0,
        "slices_written": [],
        # The app LOG is not captured for an exploratory turn; the WIRE is.
        # Two different questions, so two keys and two answers -- the
        # skipped line above is about the log alone.
        "network": case_runner.network_record(network),
    }


async def _explore_begin_network(run_id: str, state: object, ctx: Context) -> dict:
    """Start this turn's capture and put its record in the explore state.

    Returns the state to persist. Never raises: a capture is evidence, and
    evidence may not cost a turn.
    """
    body = dict(state if isinstance(state, dict) else {})
    try:
        tc_id = explore_turn_tc_id(body.get("turn"))
        prior = body.get("network")
        # A RE-ISSUED packet for the same turn (a refused script, a resumed
        # chat) must not leave the first capture running: the console records
        # until it is told to stop, and the second start would write the same
        # file name over a capture nothing had read.
        if isinstance(prior, dict) and prior.get("started"):
            await capture.abort_network(ctx.serial, run_id, tc_id, prior)
        body["network"] = case_runner.network_record(
            await capture.begin_network(ctx.serial, ctx.package, run_id, tc_id, 0)
        )
    except Exception:  # never-raise: evidence is not a turn
        logger.exception("mobile.session._explore_begin_network failed")
    return body


async def _explore_abort_network(run_id: str, state: object, ctx: Context) -> dict:
    """Stop a capture whose turn will never be replayed, and drop its record.

    Returns the state to persist. The record is REMOVED rather than kept: the
    file is gone, so a record still saying ``started`` would have the next
    begin abort something that no longer exists. Never raises.
    """
    body = dict(state if isinstance(state, dict) else {})
    prior = body.get("network")
    if not (isinstance(prior, dict) and prior.get("started")):
        return body
    try:
        await capture.abort_network(
            ctx.serial, run_id, explore_turn_tc_id(body.get("turn")), prior
        )
    except Exception:  # never-raise: evidence is not a turn
        logger.exception("mobile.session._explore_abort_network failed")
    body.pop("network", None)
    return body


async def _explore_finish_network(run_id: str, state: object, ctx: Context) -> dict:
    """Stop this turn's capture and return its record for the checkpoint.

    The record is READ from the state the packet build persisted, so a turn
    submitted from another chat still finds its own capture.
    """
    body = state if isinstance(state, dict) else {}
    try:
        tc_id = explore_turn_tc_id(body.get("turn"))
        return case_runner.network_record(
            await capture.finish_network(
                ctx.serial,
                ctx.package,
                run_id,
                tc_id,
                0,
                body.get("network"),
            )
        )
    except Exception as exc:  # never-raise: evidence is not a verdict
        logger.exception("mobile.session._explore_finish_network failed")
        # A BLANK record here is what made the report say the capture ran and
        # found nothing. The failure names itself instead, so the page shows a
        # reason rather than a false negative.
        return case_runner.network_record(
            {
                "stage": "finish",
                "skipped": (
                    "the capture could not be stopped and read for this turn: "
                    + str(exc)[:200]
                ),
            }
        )


def _persist_explore(run_id: str, state: object, planned: object = None) -> None:
    """Write the exploratory state back into the manifest, in place.

    *planned* is one ``TestCase``-shaped dict for the turn just submitted. It
    goes in the SAME read-modify-write as the state: this function writes the
    WHOLE manifest, so two separate writes would have the second clobber the
    first.
    """
    try:
        manifest = (run_store.read_manifest(run_id) or {}).get("content") or {}
        if not isinstance(manifest, dict):
            return
        manifest["explore"] = state if isinstance(state, dict) else {}
        if isinstance(planned, dict) and planned.get("tc_id"):
            cases = [
                body
                for body in list(manifest.get("cases") or [])
                if isinstance(body, dict)
                and str(body.get("tc_id") or "") != str(planned["tc_id"])
            ]
            cases.append(planned)
            manifest["cases"] = cases
            manifest["total"] = len(cases)
        run_store.write_manifest(run_id, manifest)
    except Exception:  # pragma: no cover - defensive
        logger.warning("mobile.session: could not persist explore state")
