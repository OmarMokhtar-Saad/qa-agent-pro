"""Why a capture id stopped resolving: the eviction ledger and id normalisation."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise
from handlers import capture_limits as _limits
from tools import clock

logger = logging.getLogger("tools.mcp_handlers")


def _dedup_ids(raw) -> list:
    """Stripped, non-empty ids, first occurrence wins (review M1:
    capture_ids=["cap_A", "cap_A"] used to resolve to TWO screens, which
    satisfied a two-screen prior prep with one screen)."""
    return list(
        dict.fromkeys(
            str(x or "").strip() for x in list(raw or []) if str(x or "").strip()
        )
    )


def _note_eviction(cid: str, reason: str) -> None:
    """Record why *cid* stopped resolving. Never raises."""
    with never_raise(logger, "recording a capture eviction failed", exc_info=True):
        if not cid:
            return
        _limits._CAPTURE_EVICTIONS.pop(cid, None)
        _limits._CAPTURE_EVICTIONS[cid] = {"reason": str(reason), "at": clock.now()}
        while len(_limits._CAPTURE_EVICTIONS) > _limits._CAPTURE_EVICTIONS_MAX:
            _limits._CAPTURE_EVICTIONS.pop(next(iter(_limits._CAPTURE_EVICTIONS)), None)


def _eviction_reason(cid: str) -> str:
    """The recorded reason *cid* stopped resolving, or '' when none is held
    (unknown id, or one that simply expired). Never raises."""
    with never_raise():
        entry = _limits._CAPTURE_EVICTIONS.get(cid)
        if not entry:
            return ""
        if clock.now() - float(entry.get("at") or 0) > _limits._CAPTURE_TRAY_TTL_S:
            _limits._CAPTURE_EVICTIONS.pop(cid, None)
            return ""
        return str(entry.get("reason") or "")
    return ""


def _normalised_ids(capture_ids: list | None) -> list:
    """ONE normalisation for every reader of a host-supplied ``capture_ids``
    list: stripped, order kept, duplicates collapsed. A blank survives as one
    ``""`` so the readers that report blanks can. Three readers used to carry
    their own copy of this (peek, resolvable, revive) and a fourth was about to
    -- mirrored conditions drift, so it is written once."""
    return list(
        dict.fromkeys(str(raw or "").strip() for raw in list(capture_ids or []))
    )


def _over_cap_ids(capture_ids: list | None) -> list:
    """The ids of THIS call beyond the per-call cap, derived from the same
    normalisation ``peek_captures`` slices with. A PER-CALL fact: it is
    computed from the call's own list every time and is never written to
    ``_CAPTURE_EVICTIONS``, which holds PER-ID facts. Round 4 of the release
    review found the over-cap reason recorded into that ledger, so an id sent
    beyond the cap once was described as over-cap on every later call --
    including a call that sent one id -- for the ledger's whole TTL, and a host
    sending 225 over-cap ids flushed every genuine eviction reason. A fact
    about the call travels with the call."""
    return [cid for cid in _normalised_ids(capture_ids)[_limits.tray_max() :] if cid]
