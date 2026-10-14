"""The capture tray's sweep: TTL, count cap and byte budget, oldest first."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise
from handlers import capture_limits as _limits
from handlers.capture_evict import _note_eviction
from handlers.capture_shelf import _sweep_carry_shelf
from tools import clock

logger = logging.getLogger("tools.mcp_handlers")


def _expire_stale(now: float) -> None:
    """Drop the tray entries older than the TTL."""
    for cid, item in list(_limits._CAPTURE_TRAY.items()):
        if now - float(item.get("created_at") or 0) > _limits._CAPTURE_TRAY_TTL_S:
            _limits._CAPTURE_TRAY.pop(cid, None)
            _limits._CAPTURE_EVICTIONS.pop(cid, None)


def _evict_over_count() -> None:
    """Evict oldest-first beyond the tray's count cap."""
    overflow = len(_limits._CAPTURE_TRAY) - _limits.tray_max()
    if overflow > 0:
        oldest = sorted(
            _limits._CAPTURE_TRAY.items(), key=lambda kv: kv[1].get("created_at") or 0
        )[:overflow]
        for cid, _item in oldest:
            _limits._CAPTURE_TRAY.pop(cid, None)
            _note_eviction(
                cid,
                "pushed out of the capture tray by a LATER capture: it holds "
                f"at most {_limits.tray_max()} screens, oldest first",
            )


# BYTES, not just count. 24 full-resolution screenshots are worth far
# more than 24 thumbnails, and this pool receives items popped off the
# byte-bounded shelf by revive_captures -- so without this branch the
# shelf's bound could be walked around simply by reviving.
def _evict_over_bytes() -> None:
    """Evict oldest-first beyond the tray's byte budget."""
    total = 0
    for item in _limits._CAPTURE_TRAY.values():
        total += len(item.get("data") or b"")
    if total > _limits.tray_max_bytes():
        for cid, item in sorted(
            _limits._CAPTURE_TRAY.items(), key=lambda kv: kv[1].get("created_at") or 0
        ):
            if total <= _limits.tray_max_bytes():
                break
            total -= len(item.get("data") or b"")
            _limits._CAPTURE_TRAY.pop(cid, None)
            _note_eviction(
                cid,
                "pushed out of the capture tray by a LATER capture: its "
                f"{_limits.tray_max_bytes() // (1024 * 1024)} MiB byte budget "
                "was full, oldest first",
            )


def _sweep_capture_tray() -> None:
    """Expire old tray entries and bound the tray's size. Never raises."""
    with never_raise(logger, "capture tray sweep failed", exc_info=True):
        _expire_stale(clock.now())
        _evict_over_count()
        _evict_over_bytes()
        # The carry-forward shelf has no timer either, so it inherits this
        # sweep's cadence -- which runs on EVERY prepare, capture_ids or not --
        # instead of being swept only when something is shelved or revived.
        _sweep_carry_shelf()
