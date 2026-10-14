"""Mobile session core: run-id minting and the device-claim group. Cut verbatim from tools/mobile/session_core.py (C5 w17 sessA)."""

from __future__ import annotations

import logging
import secrets
import time
from typing import NamedTuple

from tools.mobile import (
    run_finalize,
    run_store,
)
from tools.mobile.session_core_state import _RUN_PREFIX, STATE_TAKEN_OVER

logger = logging.getLogger("tools.mobile.session")


class ClaimView(NamedTuple):
    """The fields of the ``claim`` success envelope."""

    token: str
    state: str
    holder: str
    taken_over_from: str = ""


def mint_session() -> str:
    """A lease identity for one chat. Matches ``run_store``'s session pattern."""
    return "s-" + secrets.token_hex(8)


def mint_run_id() -> str:
    """A run id a tester can read back to us. Matches ``run_store.valid_run_id``."""
    return _RUN_PREFIX + time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)


def _lease_token(session_token: str) -> tuple[str, bool]:
    """The caller's token, or a freshly minted one (and ``True``) when absent."""
    token = str(session_token or "").strip()
    fresh = not token
    if fresh:
        token = mint_session()
    return token, fresh


def _claim_after(run_id: str, token: str, result: dict) -> dict:
    """The reply once ``acquire_lease`` answered without an error."""
    body = result.get("content") or {}
    if not body.get("acquired"):
        # The displaced holder's exit. Returning HELD here would let this
        # chat keep producing packets for an emulator another chat is
        # driving, which is the whole failure the lease exists to stop.
        holder = str(body.get("holder") or "")
        return _claim_reply(ClaimView(token, STATE_TAKEN_OVER, holder))
    status = run_store.touch_lease(run_id, token)
    state = str((status.get("content") or {}).get("state") or run_store.HELD)
    if state == run_store.TAKEN_OVER:
        holder = str((status.get("content") or {}).get("holder") or "")
        return _claim_reply(ClaimView(token, STATE_TAKEN_OVER, holder))
    prior = str(body.get("taken_over_from") or "")
    return _claim_reply(ClaimView(token, run_store.HELD, token, prior))


# Take or refresh this chat's hold on a run.
#
# ``{"error", "content": {"session_token", "state", "holder",
# "taken_over_from"}}`` where ``state`` is ``run_store.HELD`` or
# :data:`STATE_TAKEN_OVER`.
#
# A caller with NO token is a fresh chat, and a fresh chat naming a run id is
# an explicit resume, so it takes over (``force=True``). A caller with a token
# that is no longer the holder is told so and gets no packet -- that decision
# is here rather than in the handler because both handlers need it and a
# duplicated "am I still the holder?" test is how the two drift.
def claim(run_id: str, session_token: str = "", *, force: bool = False) -> dict:
    """Take or refresh this chat's hold on a run."""
    try:
        refusal = _claim_refusal(run_id)
        if refusal:
            return refusal
        token, fresh = _lease_token(session_token)
        lease = run_store.LeaseOpts(force=bool(force or fresh))
        result = run_store.acquire_lease(run_id, token, lease)
        if result.get("error"):
            return result
        return _claim_after(run_id, token, result)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.session.claim failed")
        return {"error": str(exc), "content": None}


def _claim_reply(view: ClaimView) -> dict:
    """The ``claim`` success envelope."""
    return {
        "error": None,
        "content": {
            "session_token": view.token,
            "state": view.state,
            "holder": view.holder,
            "taken_over_from": view.taken_over_from,
        },
    }


def _missing_run(run_id: str) -> dict:
    """The error reply for a run id with no manifest on this machine."""
    return {
        "error": (
            "No run `" + str(run_id)[:64] + "` on this machine. "
            "Nothing was created. `qa_mobile_status` lists the runs "
            "this install knows about."
        ),
        "content": None,
    }


def _ended_run(run_id: str, stopped: str) -> dict:
    """The error reply for a run that was stopped (``stopped`` is the reason)."""
    return {
        "error": (
            "Run `" + str(run_id)[:64] + "` has ended (" + stopped[:40] + ") "
            "and takes no more steps. `qa_mobile_status` shows its result; "
            "start a new run to test again."
        ),
        "content": None,
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
        return _missing_run(run_id)
    stopped = run_finalize.stop_text(on_disk)
    if stopped:
        # A run ended by qa_mobile_stop or the idle release takes no more
        # steps: its verdict is recorded, and a later step would restart
        # the heartbeat and retake the device under a run that says it ended.
        return _ended_run(run_id, stopped)
    return None
