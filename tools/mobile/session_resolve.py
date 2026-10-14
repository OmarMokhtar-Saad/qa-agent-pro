"""Mobile session: ``resolve``, where a run is, read from disk only.

Cut from tools/mobile/session_runs.py (C4 A2) so ``render`` can import it at
the top without reaching ``explore_runner``: the explore stop producer is
``explore_stop.stop_reason``, the same function ``explore_runner`` re-exports.
"""

from __future__ import annotations

import logging
import math

from tools.mobile import run_finalize, run_store, scheduler
from tools.mobile.explore_stop import stop_reason
from tools.mobile.session_core import (
    LANE_EXPLORE,
    LANE_SUITE,
    STATE_ABANDONED,
    STATE_GATE,
    STATE_REPORT,
    STATE_RUNNING,
)
from tools.mobile.session_records import device_record

logger = logging.getLogger("tools.mobile.session")


def _lane_progress(run_id: str, manifest: dict, explore: dict) -> tuple[str, dict, str]:
    """``(state, scheduler point, explore stop)`` before the heartbeat check."""
    # PUBLISHED, not just branched on: every consumer reads the one producer.
    if str(manifest.get("lane") or "") == LANE_EXPLORE:
        explore_stop = stop_reason(explore)
        return (STATE_REPORT if explore_stop else STATE_RUNNING), {}, explore_stop
    point = (scheduler.next_case(run_id) or {}).get("content") or {}
    if point.get("finished") or run_finalize.stop_text(manifest):
        return STATE_REPORT, point, ""
    if point.get("gate"):
        return STATE_GATE, point, ""
    return STATE_RUNNING, point, ""


def _lease_age(lease: dict) -> float:
    """The lease's heartbeat age; a non-finite one reads as ZERO."""
    # A non-finite age reads as ZERO, i.e. "just refreshed", which is the
    # SAFE direction here: it leaves the run alone rather than declaring
    # somebody else's live session abandoned. Left unguarded, a NaN makes
    # the comparison in `_is_abandoned` False and the run could never be
    # reclaimed at all, because every comparison against NaN is False.
    age = float(lease.get("age") or 0.0)
    return age if math.isfinite(age) else 0.0


def _is_abandoned(state: str, lease: dict, lease_age: float) -> bool:
    """An unfinished run whose heartbeat stopped."""
    # D7 (2026-09-03): the lease's own heartbeat age is the signal; STALE is
    # the same threshold a takeover uses, so the state and the takeover rule
    # cannot drift apart.
    return (
        state not in (STATE_REPORT, STATE_GATE)
        and str(lease.get("state") or run_store.NONE) != run_store.NONE
        and lease_age > run_store.LEASE_STALE_S
    )


def _identity_fields(run_id: str, state: str, manifest: dict) -> dict:
    """The run's identity keys, from its manifest."""
    return {
        "run_id": str(run_id),
        "state": state,
        "lane": str(manifest.get("lane") or LANE_SUITE),
        "package": str(manifest.get("package") or ""),
        "serial": str(manifest.get("serial") or ""),
        "device": device_record(manifest.get("device")),
        "avd": str(manifest.get("avd") or ""),
        "source": str(manifest.get("source") or ""),
        "total": int(manifest.get("total") or 0),
    }


def _point_fields(point: dict) -> dict:
    """The scheduler point's keys."""
    return {
        "done": int(point.get("done") or 0),
        "failed": list(point.get("failed") or []),
        "next_tc_id": str(point.get("tc_id") or ""),
        "gate": bool(point.get("gate")),
    }


def _finished(is_explore: bool, explore_stop: str, point: dict) -> bool:
    """Whether the run is finished. An explore run has no scheduler point."""
    # The explore lane has no `point`, so this was False for the whole life of
    # every exploratory run; its producer is `stop_reason`.
    return bool(explore_stop) if is_explore else bool(point.get("finished"))


def _lease_fields(lease: dict, lease_age: float) -> dict:
    """The lease's keys."""
    return {
        "holder": str(lease.get("holder") or ""),
        "lease_state": str(lease.get("state") or run_store.NONE),
        "lease_age": lease_age,
    }


def _resolve_body(run_id: str, manifest: dict, lease: dict) -> dict:
    """The ``content`` of a resolved run, from its manifest and lease."""
    explore = manifest.get("explore")
    explore = explore if isinstance(explore, dict) else {}
    state, point, explore_stop = _lane_progress(run_id, manifest, explore)
    lease_age = _lease_age(lease)
    if _is_abandoned(state, lease, lease_age):
        state = STATE_ABANDONED
    is_explore = str(manifest.get("lane") or "") == LANE_EXPLORE
    return {
        **_identity_fields(run_id, state, manifest),
        **_point_fields(point),
        "finished": _finished(is_explore, explore_stop, point),
        "explore_stop": explore_stop,
        "final": run_finalize.recorded(manifest),
        "explore": explore,
        **_lease_fields(lease, lease_age),
    }


def _failure(message: str) -> dict:
    """A refusal envelope."""
    return {"error": message, "content": None}


def _no_run_message(run_id: str) -> str:
    """The text for a run id nothing on this machine matches."""
    return (
        "No run `"
        + str(run_id)
        + "` on this machine. `qa_mobile_status` with no run id "
        "lists the runs that do exist."
    )


def _lease_of(run_id: str, session_token: str) -> dict:
    """The run's lease status body."""
    token = str(session_token or "-")
    return (run_store.lease_status(run_id, token) or {}).get("content") or {}


def resolve(run_id: str, session_token: str = "") -> dict:
    """Where this run is, read from disk ONLY.

    Nothing about this function's answer depends on anything a previous call
    left in memory, which is what makes every state re-entrant from a fresh
    chat given only a ``run_id``.
    """
    try:
        if not run_store.valid_run_id(run_id):
            return _failure("Refusing " + repr(str(run_id)[:40]) + " as a run id.")
        manifest = (run_store.read_manifest(run_id) or {}).get("content")
        if not isinstance(manifest, dict) or not manifest:
            return _failure(_no_run_message(run_id))
        lease = _lease_of(run_id, session_token)
        return {"error": None, "content": _resolve_body(run_id, manifest, lease)}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.session.resolve failed")
        return _failure(str(exc))
