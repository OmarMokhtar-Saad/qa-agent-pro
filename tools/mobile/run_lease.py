"""The run lease: take, inspect and refresh it, and tell a displaced chat.

:func:`acquire_lease` takes or refreshes the run's lease and returns
``{"error", "content": {"acquired", "holder", "taken_over_from", "reason"}}``.

Take-over happens when the current holder's heartbeat is older than
:data:`LEASE_STALE_S`, or when the caller passes ``force=True`` -- which is
what an explicit "resume run X" from a second chat means. The displaced
holder is recorded in ``taken_over_from``, and that is what makes its NEXT
:func:`touch_lease` return ``taken_over`` instead of silently continuing to
drive the same emulator.

**This is read-decide-write and therefore not atomic as a whole.** The
compare-after-swap at the end narrows the window: after writing, we re-read
and, if another session already won, return ``acquired=False`` with
``reason="lost_race"`` rather than handing the caller a lease it does not
hold. It does NOT close the window -- a loser whose re-read lands before the
winner's write still sees itself and learns only at its next
:func:`touch_lease`. Both behaviours are pinned by tests. The residual is
bounded by the per-emulator lock in ``tools/mobile/locks.py``: two chats can
briefly disagree about who owns a RUN RECORD, but not about who is driving
the DEVICE. A file lock around the acquire itself was considered and
rejected for this phase -- it trades a bounded, disclosed, tested race for
an undisclosed stale-guard-file failure mode on a path no MCP tool can even
reach yet, and Phase 3 is where the lock/lease pairing gets its owner.

:func:`touch_lease` refreshes the heartbeat, or reports that the lease was
taken over. This is the call every packet-producing step makes first. A
session that lost the lease is told ``state="taken_over"`` and the file is
NOT written, so the new holder's heartbeat cannot be clobbered by the old one.

:func:`takeover_message` is the line the displaced chat is shown. No secret,
no path, no run data.
"""

from __future__ import annotations

import logging

from tools.mobile import run_consts, run_io, run_lease_core

logger = logging.getLogger("tools.mobile.run_store")


def _acquire(claim: run_lease_core._Claim) -> dict:
    current = run_io._read_json(run_lease_core._lease_path(claim.run_id))
    current = current if isinstance(current, dict) else None
    decision = run_lease_core._lease_decision(current, claim)
    reason, taken_from = decision
    if reason == "held_by_other":
        return run_lease_core._lease_answer(False, taken_from, ("", reason))
    winner = run_lease_core._swap_lease(claim, current, decision)
    if winner and winner != claim.session_id:
        return run_lease_core._lease_answer(False, winner, ("", "lost_race"))
    return run_lease_core._lease_answer(True, claim.session_id, (taken_from, reason))


def acquire_lease(
    run_id: str, session_id: str, opts: run_lease_core.LeaseOpts | None = None
) -> dict:
    """Take or refresh the run's lease."""
    opts = opts or run_lease_core.LeaseOpts()
    try:
        refusal = run_lease_core._lease_input_error(run_id, session_id)
        if refusal:
            return {"error": refusal, "content": None}
        claim = run_lease_core._Claim(
            run_id, session_id, run_io._now(opts.now), opts.force
        )
        return _acquire(claim)
    except Exception as exc:
        logger.exception("mobile.run_store.acquire_lease failed")
        return {"error": str(exc), "content": None}


def _none_status() -> dict:
    return {
        "error": None,
        "content": {
            "state": run_consts.NONE,
            "holder": "",
            "taken_over_from": "",
            "age": 0.0,
        },
    }


def _held_status(current: dict, session_id: str, now: float | None) -> dict:
    holder = str(current.get("session_id") or "")
    age = run_io._now(now) - float(current.get("heartbeat") or 0)
    state = run_consts.HELD if holder == session_id else run_consts.TAKEN_OVER
    return {
        "error": None,
        "content": {
            "state": state,
            "holder": holder,
            "taken_over_from": str(current.get("taken_over_from") or ""),
            "age": age,
            "released": bool(current.get("released_at")),
        },
    }


def lease_status(run_id: str, session_id: str, *, now: float | None = None) -> dict:
    """``{state, holder, taken_over_from, age}`` from *session_id*'s point of view."""
    try:
        if not run_io.valid_run_id(run_id):
            return {"error": "Invalid run id.", "content": None}
        current = run_io._read_json(run_lease_core._lease_path(run_id))
        if not isinstance(current, dict) or not current.get("session_id"):
            return _none_status()
        return _held_status(current, session_id, now)
    except Exception as exc:
        logger.exception("mobile.run_store.lease_status failed")
        return {"error": str(exc), "content": None}


def _refreshed(content: dict, refreshed: dict) -> dict:
    content["state"] = run_consts.HELD
    content["taken_over_from"] = str(
        (refreshed["content"] or {}).get("taken_over_from") or ""
    )
    content["age"] = 0.0
    return content


def touch_lease(run_id: str, session_id: str, *, now: float | None = None) -> dict:
    """Refresh the heartbeat, or report that the lease was taken over."""
    try:
        status = lease_status(run_id, session_id, now=now)
        if status.get("error"):
            return status
        content = dict(status["content"] or {})
        if content.get("state") == run_consts.HELD:
            refreshed = acquire_lease(
                run_id, session_id, run_lease_core.LeaseOpts(now=now)
            )
            if refreshed.get("error"):
                return refreshed
            content = _refreshed(content, refreshed)
        return {"error": None, "content": content}
    except Exception as exc:
        logger.exception("mobile.run_store.touch_lease failed")
        return {"error": str(exc), "content": None}


def takeover_message(run_id: str, holder: str) -> str:
    """The line the displaced chat is shown. No secret, no path, no run data."""
    return (
        "⚠️ This run was taken over by another chat, so this one has "
        "stopped driving the emulator. Run `" + str(run_id) + "` is now held by "
        "session `" + str(holder)[:40] + "`. Continue there, or start a new run "
        "here."
    )
