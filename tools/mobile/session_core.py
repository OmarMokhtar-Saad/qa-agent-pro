"""Mobile session: states, call budget, run-id minting and the device-claim group. Cut verbatim from tools/mobile/session.py (P4.4)."""

from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass

from tools.mobile import (
    run_finalize,
    run_store,
)

logger = logging.getLogger("tools.mobile.session")


STATE_NEEDS_FLAG = "needs_flag"
STATE_PROVISIONING = "provisioning"
#: ``ensure_run_device`` only: the run's device is gone and the AVD it came from
#: no longer exists, so there is nothing to re-boot. Never written to a manifest;
#: its one consumer, ``mcp_handlers._mobile_resume_device_stage``, answers it
#: with the no-AVD setup guide.
STATE_NO_AVD = "no_avd"
STATE_BOOTING = "booting"
STATE_NEEDS_APP = "needs_app"
STATE_PREFLIGHT = "preflight"
STATE_MENU = "menu"
STATE_RUNNING = "running"
STATE_GATE = "gate"
STATE_REPORT = "report"
STATE_TAKEN_OVER = "taken_over"
STATE_BUSY = "busy"

#: An unfinished run whose lease heartbeat stopped (2026-09-03, D7). NOT a
#: takeover: nobody else holds it, the chat that did simply went away -- so the
#: tester is told it can be picked up rather than that it is gone. Distinct
#: from STATE_TAKEN_OVER, which names a run another chat IS driving.
STATE_ABANDONED = "abandoned"

#: Every state a handler can be in, in flow order. Also the set the tests assert
#: against, so a state that silently stops being reachable fails the suite.
STATES: tuple[str, ...] = (
    STATE_NEEDS_FLAG,
    STATE_PROVISIONING,
    STATE_BOOTING,
    STATE_NEEDS_APP,
    STATE_PREFLIGHT,
    STATE_MENU,
    STATE_RUNNING,
    STATE_GATE,
    STATE_ABANDONED,
    STATE_REPORT,
    STATE_TAKEN_OVER,
    STATE_BUSY,
)

#: The longest ONE tool call may run before it hands back a pointer instead of
#: an answer. Clients kill a tool call at around 50 seconds and a killed call is
#: indistinguishable from a broken server, so this bound is STRUCTURAL: it is
#: passed in, checked at the two places that start slow device work, and never
#: asserted against wall-clock in a test -- a test that measured elapsed time
#: here would be asserting below its own noise floor.
CALL_BUDGET_S = 45


#: The tail of :data:`CALL_BUDGET_S` reserved, once, for one best-effort
#: screen capture when a busy reply is about to go out with nothing in it.
#: ``next_packet`` spends it on :func:`_final_packet` instead of starting the
#: slow relaunch/foreground-poll path this reserve exists to avoid overrunning.
PACKET_RESERVE_S = 8


LANE_SUITE = "suite"
LANE_EXPLORE = "explore"

_RUN_PREFIX = "mrun-"


@dataclass(frozen=True)
class Budget:
    """A monotonic deadline for ONE tool call.

    A value object, so a test constructs an ALREADY-EXPIRED budget instead of
    waiting for one -- which is what keeps the early-return tests structural.
    """

    deadline: float

    def remaining(self) -> float:
        return max(0.0, float(self.deadline) - time.monotonic())

    def expired(self) -> bool:
        return time.monotonic() >= float(self.deadline)


def new_budget(seconds: float = CALL_BUDGET_S) -> Budget:
    """A budget starting now, never longer than :data:`CALL_BUDGET_S`."""
    span = min(float(seconds or CALL_BUDGET_S), float(CALL_BUDGET_S))
    return Budget(deadline=time.monotonic() + max(1.0, span))


def _busy(resolved: object, tc_id: str, *, packet: object = None) -> dict:
    """The bounded-call reply: usually a pointer, and never half-done.

    *packet* is the reserved-tail capture from :func:`_final_packet` -- a
    best-effort screen, or ``None`` when the device did not answer inside
    :data:`PACKET_RESERVE_S`. Passing it through here, rather than building it
    at the call site, keeps every caller's shape the same.
    """
    return {
        "error": None,
        "content": {
            "state": STATE_BUSY,
            "packet": packet,
            "tc_id": str(tc_id or ""),
            "resolved": resolved if isinstance(resolved, dict) else {},
        },
    }


def _in_reserve(budget: object) -> bool:
    """Expired, or inside the reserved tail. ``expired()`` is asked first so a
    budget without ``remaining()`` (a duck-typed test budget) still works."""
    if budget.expired():
        return True
    remaining = getattr(budget, "remaining", None)
    return callable(remaining) and remaining() <= PACKET_RESERVE_S


# What reading and walking a stored case dump can raise: a file that will not
# read, or a record of an unexpected shape.
_CASE_STORE_ERRORS = (OSError, ValueError, TypeError, AttributeError, KeyError)


def mint_session() -> str:
    """A lease identity for one chat. Matches ``run_store``'s session pattern."""
    return "s-" + secrets.token_hex(8)


def mint_run_id() -> str:
    """A run id a tester can read back to us. Matches ``run_store.valid_run_id``."""
    return _RUN_PREFIX + time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)


def claim(run_id: str, session_token: str = "", *, force: bool = False) -> dict:
    """Take or refresh this chat's hold on a run.

    ``{"error", "content": {"session_token", "state", "holder",
    "taken_over_from"}}`` where ``state`` is ``run_store.HELD`` or
    :data:`STATE_TAKEN_OVER`.

    A caller with NO token is a fresh chat, and a fresh chat naming a run id is
    an explicit resume, so it takes over (``force=True``). A caller with a token
    that is no longer the holder is told so and gets no packet -- that decision
    is here rather than in the handler because both handlers need it and a
    duplicated "am I still the holder?" test is how the two drift.
    """
    try:
        refusal = _claim_refusal(run_id)
        if refusal:
            return refusal
        token = str(session_token or "").strip()
        fresh = not token
        if fresh:
            token = mint_session()
        result = run_store.acquire_lease(run_id, token, force=bool(force or fresh))
        if result.get("error"):
            return result
        body = result.get("content") or {}
        if not body.get("acquired"):
            # The displaced holder's exit. Returning HELD here would let this
            # chat keep producing packets for an emulator another chat is
            # driving, which is the whole failure the lease exists to stop.
            return _claim_reply(token, STATE_TAKEN_OVER, str(body.get("holder") or ""))
        status = run_store.touch_lease(run_id, token)
        state = str((status.get("content") or {}).get("state") or run_store.HELD)
        if state == run_store.TAKEN_OVER:
            holder = str((status.get("content") or {}).get("holder") or "")
            return _claim_reply(token, STATE_TAKEN_OVER, holder)
        return _claim_reply(
            token,
            run_store.HELD,
            token,
            str(body.get("taken_over_from") or ""),
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.session.claim failed")
        return {"error": str(exc), "content": None}


def _claim_reply(
    token: str, state: str, holder: str, taken_over_from: str = ""
) -> dict:
    """The ``claim`` success envelope."""
    return {
        "error": None,
        "content": {
            "session_token": token,
            "state": state,
            "holder": holder,
            "taken_over_from": taken_over_from,
        },
    }


def _claim_refusal(run_id: str) -> dict | None:
    """The error reply when ``run_id`` cannot be claimed (absent or ended), else None."""
    # EXISTENCE FIRST. acquire_lease mkdir -p's the run directory, so
    # claiming before checking let any well-formed id mint a run in the
    # shared state root -- and the reply then said the run did not exist
    # while its lease sat on disk. The check lives in claim rather than in the
    # handlers: two copies of one test is how the two handlers drift.
    on_disk = run_store.read_manifest(run_id).get("content") or {}
    if not on_disk:
        return {
            "error": (
                "No run `" + str(run_id)[:64] + "` on this machine. "
                "Nothing was created. `qa_mobile_status` lists the runs "
                "this install knows about."
            ),
            "content": None,
        }
    stopped = run_finalize.stop_text(on_disk)
    if stopped:
        # A run ended by qa_mobile_stop or the idle release takes no more
        # steps: its verdict is recorded, and a later step would restart
        # the heartbeat and retake the device under a run that says it ended.
        return {
            "error": (
                "Run `" + str(run_id)[:64] + "` has ended (" + stopped[:40] + ") "
                "and takes no more steps. `qa_mobile_status` shows its result; "
                "start a new run to test again."
            ),
            "content": None,
        }
    return None
