"""Prep store settings, TTL and size decisions (leaf of ``tools.prep_store``)."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from tools import settings_port
from tools.install_paths import resolve_data_path

logger = logging.getLogger("tools.prep_store")

_DEFAULT_TTL_S = 3600
_DEFAULT_MAX_BYTES = 4_000_000
# Hard cap on TOTAL prep lifetime under the sliding TTL (see _expired):
# touch refreshes restart the TTL clock but can never push a prep past
# created_at + this. 4x the default TTL.
_DEFAULT_MAX_LIFETIME_S = 14_400

# Seconds a contended write waits before giving up. Host mode means several MCP
# host processes share this DB file, so a short wait beats an instant failure.
_BUSY_TIMEOUT_S = 5.0


def _db_path() -> Path:
    # Anchored to the install root, not the process cwd: a prep written while one
    # project was open used to be invisible when another was, which the host reads
    # as "prep not found -- start again" and answers with a full re-generation.
    return resolve_data_path(settings_port.get().qa_suite_store_path)


def _ttl_seconds() -> float:
    """TTL after which a prep record is expired on read. Never raises."""
    val = getattr(settings_port.get(), "qa_prep_ttl_s", _DEFAULT_TTL_S)
    try:
        val = int(val)
        # Inside the guard: int(10**400) is exact, and float() of it is
        # the OverflowError. Measured -- this function promised never to
        # raise and did.
        return float(val) if val > 0 else float(_DEFAULT_TTL_S)
    except (TypeError, ValueError, OverflowError):
        return float(_DEFAULT_TTL_S)


def _sliding_ttl_on() -> bool:
    """The sliding TTL is unconditional since 2026-08-12
    (QA_PREP_SLIDING_TTL_ENABLED was deleted; it had soaked ON since
    2026-08-01)."""
    return True


def _touch_enabled() -> bool:
    """Whether an activity touch is RECORDED in preps.touched_at.

    Unconditional since 2026-08-12. The sliding TTL needs the timestamp to
    enforce a refreshed clock; DISCLOSURE needs it to see the incident at
    all -- the 2026-07-31 shape was 8 worker packets fetched via
    qa_get_category_job and ZERO qa_submit_category calls, so staged == 0 and
    touched_at is the only evidence the run ever existed. Never raises."""
    return True


def _max_lifetime_s() -> float:
    """Hard cap on a prep's TOTAL lifetime under the sliding TTL, so touch
    refreshes cannot extend a prep forever (the same anti-extension stance
    update_prep takes for the gap loop). Never raises."""
    val = getattr(
        settings_port.get(), "qa_prep_max_lifetime_s", _DEFAULT_MAX_LIFETIME_S
    )
    try:
        val = int(val)
        return float(val) if val > 0 else float(_DEFAULT_MAX_LIFETIME_S)
    except (TypeError, ValueError, OverflowError):
        return float(_DEFAULT_MAX_LIFETIME_S)


def _expired(created_at: float, touched_at: Any) -> bool:
    """TTL decision for one prep row. Fixed TTL from created_at by default;
    with QA_PREP_SLIDING_TTL_ENABLED the clock restarts at the last touch
    (qa_get_category_job / qa_submit_category), bounded by _max_lifetime_s()
    from creation. A NULL/absent touched_at simply degrades to the fixed TTL,
    so a deferred migration can never expire a live prep. Never raises."""
    now = time.time()
    try:
        anchor = created_at
        if _sliding_ttl_on():
            try:
                t = float(touched_at or 0.0)
            except (TypeError, ValueError, OverflowError):
                t = 0.0
            anchor = max(created_at, t)
            if (now - created_at) > _max_lifetime_s():
                return True
        return (now - anchor) > _ttl_seconds()
    except (ArithmeticError, TypeError):  # pragma: no cover -- arithmetic on floats
        return (now - created_at) > _ttl_seconds()


def _max_bytes() -> int:
    """Max serialized payload size accepted by save_prep/save_submission."""
    val = getattr(settings_port.get(), "qa_prep_max_bytes", _DEFAULT_MAX_BYTES)
    try:
        val = int(val)
    except (TypeError, ValueError, OverflowError):
        return _DEFAULT_MAX_BYTES
    return val if val > 0 else _DEFAULT_MAX_BYTES


def _payload_too_big(payload: dict) -> bool:
    """Whether the serialized payload is over the QA_PREP_MAX_BYTES cap."""
    blob = json.dumps(payload or {})
    return len(blob.encode("utf-8")) > _max_bytes()


def _expires_at(created_f: float, touched_f: float) -> float:
    anchor = max(created_f, touched_f)
    if _sliding_ttl_on():
        return min(anchor + _ttl_seconds(), created_f + _max_lifetime_s())
    return created_f + _ttl_seconds()
