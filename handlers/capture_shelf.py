"""The carry-forward shelf: where a shipped capture waits to be revived."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise
from handlers import capture_limits as _limits
from handlers.capture_evict import _note_eviction
from tools import clock

logger = logging.getLogger("tools.mcp_handlers")


def _shelf_expired(now: float) -> None:
    """Drop the shelf entries older than the tray's TTL."""
    for cid, item in list(_limits._CARRY_SHELF.items()):
        if now - float(item.get("created_at") or 0) > _limits._CAPTURE_TRAY_TTL_S:
            _limits._CARRY_SHELF.pop(cid, None)
            _limits._CAPTURE_EVICTIONS.pop(cid, None)


def _shelf_over_count() -> None:
    """Evict oldest-first beyond the shelf's count cap."""
    overflow = len(_limits._CARRY_SHELF) - _limits.shelf_max()
    if overflow > 0:
        oldest = sorted(
            _limits._CARRY_SHELF.items(), key=lambda kv: kv[1].get("created_at") or 0
        )[:overflow]
        for cid, _item in oldest:
            _limits._CARRY_SHELF.pop(cid, None)
            _note_eviction(
                cid,
                "pushed off the carry-forward shelf by a LATER prepare: it "
                f"holds at most {_limits.shelf_max()} shipped screens, oldest first",
            )


def _shelf_over_bytes() -> None:
    """Evict oldest-first beyond the shelf's byte budget."""
    total = 0
    for item in _limits._CARRY_SHELF.values():
        total += len(item.get("data") or b"")
    if total > _limits.shelf_max_bytes():
        for cid, item in sorted(
            _limits._CARRY_SHELF.items(), key=lambda kv: kv[1].get("created_at") or 0
        ):
            if total <= _limits.shelf_max_bytes():
                break
            total -= len(item.get("data") or b"")
            _limits._CARRY_SHELF.pop(cid, None)
            _note_eviction(
                cid,
                "pushed off the carry-forward shelf by a LATER prepare: its "
                "byte budget was full, oldest first",
            )


def _sweep_carry_shelf() -> None:
    """Expire old shelf entries and bound the shelf by COUNT and by BYTES.

    A count cap alone is not a memory bound -- 24 full-resolution screenshots
    are worth far more than 24 thumbnails -- so the byte cap evicts oldest-first
    until the total fits. Never raises."""
    with never_raise(logger, "carry shelf sweep failed", exc_info=True):
        _shelf_expired(clock.now())
        _shelf_over_count()
        _shelf_over_bytes()


def _shelve_capture(cid: str, item: dict) -> None:
    """Park ONE shipped tray entry on the carry-forward shelf. Never raises."""
    with never_raise(logger, "shelving a captured screen failed", exc_info=True):
        if cid and isinstance(item, dict) and item.get("data"):
            _limits._CARRY_SHELF[cid] = item
        _sweep_carry_shelf()


def _drop_captures(capture_ids: list | None) -> None:
    """Remove tray entries once their screens have ACTUALLY shipped on a payload.

    Called only immediately before a successful return, so a retried prepare
    cannot double-count the same screen against the image byte budget while a
    FAILED prepare leaves them intact. Idempotent, never raises."""
    with never_raise(logger, "dropping captured screens failed", exc_info=True):
        for raw in list(capture_ids or []):
            cid = str(raw or "").strip()
            if cid:
                # MOVED to the carry-forward shelf, not freed (2026-08-09): a
                # re-prepare of the same source -- or a re-sent id -- can revive
                # these exact screens. The tray contract is unchanged: the id no
                # longer resolves through peek_captures.
                item = _limits._CAPTURE_TRAY.pop(cid, None)
                if item:
                    _shelve_capture(cid, item)
