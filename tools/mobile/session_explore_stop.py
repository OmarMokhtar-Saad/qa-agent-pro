"""Mobile session explore lane: guard-stop lookup for confirms (split from session_explore)."""

from __future__ import annotations

import logging

from tools.mobile import run_store
from tools.mobile.session_core import _CASE_STORE_ERRORS

logger = logging.getLogger("tools.mobile.session")


# Scoped to the ONE case being submitted -- an exact match on ``tc_id`` --
# not a run-wide "latest by tc_id digit" scan: that scan's "latest" case
# can be a DIFFERENT tc_id than the one this confirm names, which would let
# a stop recorded against one case authorise a confirm submitted for
# another. The scripted lane passes its own ``tc_id`` and stops here. The
# explore lane no longer calls this function directly -- its turn counter
# advances on EVERY packet, not only across a resubmission, so an exact
# match alone misses a confirm submitted after the scheduler moved on; see
# :func:`_prior_guard_stop_explore`, which tries this exact match FIRST
# and only then falls back.
#
# FAILS CLOSED: any read that raises, or a matching case that recorded no
# stop, is untracked -- ``{}`` -- rather than treated as leniently as
# :func:`_explore_prior_turn_blocked` treats its own question. That
# sibling fails OPEN because its wrong "no" only costs one extra retry;
# this one's wrong "no" would let a mismatched confirm through, which is
# the defect this function exists to close.
def _prior_guard_stop(run_id: str, tc_id: str) -> dict:
    """THIS case's own last recorded guard-stop fingerprint, or ``{}``."""
    try:
        listed = run_store.list_cases(run_id) or {}
        for body in listed.get("content") or []:
            if isinstance(body, dict) and str(body.get("tc_id") or "") == str(tc_id):
                stop = body.get("guard_stop")
                return dict(stop) if isinstance(stop, dict) else {}
    except _CASE_STORE_ERRORS:  # pragma: no cover - defensive
        logger.warning("mobile.session: could not read this case's last guard stop")
    return {}


def _highest_turn_stop(listed: dict) -> dict:
    """The highest-turn checkpointed case's own ``guard_stop``, or ``{}``."""
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


# The explore lane's own guard-stop lookup: an EXACT match on ``tc_id``
# first (the common case, a resubmission of the SAME turn), and only when
# that misses, the highest-turn checkpointed case's own ``guard_stop`` --
# verbatim, so a confirm still spends against exactly what that turn
# recorded (``{}`` if that turn recorded none, which keeps the one-shot
# confirm inert rather than manufacturing a stop for it to consume).
#
# This exists because the explore lane's turn counter
# (``explore_runner.next_packet``) bumps ``state["turn"]`` on EVERY call,
# not only across a resubmission of the same turn -- so a confirm
# submitted after the scheduler already advanced past this turn names a
# ``tc_id`` no case was ever checkpointed under, and the exact-match lookup
# alone returns ``{}`` and the guard fires again on a turn that already
# confirmed. The scripted lane never reaches this: it always has an exact
# ``tc_id`` match once a case exists, so :func:`_prior_guard_stop` alone
# still serves it directly. FAILS CLOSED the same way its sibling does:
# any read that raises, or no checkpointed case at all, is untracked --
# ``{}``.
def _prior_guard_stop_explore(run_id: str, tc_id: str) -> dict:
    """The explore lane's own guard-stop lookup: exact ``tc_id`` first, else highest turn."""
    exact = _prior_guard_stop(run_id, tc_id)
    if exact:
        return exact
    try:
        return _highest_turn_stop(run_store.list_cases(run_id) or {})
    except _CASE_STORE_ERRORS:  # pragma: no cover - defensive
        logger.warning(
            "mobile.session: could not read the latest explore turn's guard stop"
        )
        return {}
