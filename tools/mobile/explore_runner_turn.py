"""One exploratory turn: the stop check, the turn-number ledger, the packet.

Cut from ``tools.mobile.explore_runner``; ``next_turn`` (the facade) drives these.
Never imports the facade.
"""

from __future__ import annotations

from tools.mobile import run_packets as mobile_run
from tools.mobile.explore_runner_consts import RUNNING
from tools.mobile.explore_runner_state import _record_scope, remaining
from tools.mobile.explore_runner_types import Held, TurnParts
from tools.mobile.explore_stop import stop_reason


def turn_result(status: str, body: dict, parts: TurnParts) -> dict:
    """The ``next_turn`` success envelope for *status*."""
    return {
        "error": None,
        "content": {"status": status, "state": body, **parts._asdict()},
    }


def halt_result(body: dict, now: float | None) -> dict | None:
    """The stop envelope when *body* must stop now, else ``None``."""
    stop = stop_reason(body, now=now)
    if stop:
        body["stop"] = stop
        return turn_result(stop, body, TurnParts(None, None, remaining(body, now=now)))
    return None


def packet_turn(run_id: str, held: Held, now: float | None) -> dict:
    """Record scope, allocate the turn number and build the running turn's packet."""
    body, screen = held
    _record_scope(body, screen)
    _allocate_turn(body)
    left = remaining(body, now=now)
    packet = mobile_run.build_explore_turn(
        body, screen, mobile_run.ExploreRef(run_id=run_id, remaining=left)
    )
    return turn_result(RUNNING, body, TurnParts(screen, packet, left))


def _committed_turn(body: dict) -> int:
    """The highest COMMITTED turn number in *body*, with legacy fallbacks."""
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
        return int(committed or 0)
    except (TypeError, ValueError, OverflowError):
        return int(body.get("turn") or 0)


# Commit the previous turn and hand out the PROVISIONAL next turn number.
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
def _allocate_turn(body: dict) -> None:
    """Commit the previous turn and hand out the PROVISIONAL next turn number."""
    committed = _committed_turn(body)
    body["committed_turn"] = committed
    body["turn"] = committed + 1
