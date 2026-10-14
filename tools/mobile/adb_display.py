"""Display size: ``display_size`` and its ``wm size`` parsers (moved from adb.py)."""

from __future__ import annotations

import logging

from tools.mobile.adb_display_state import _DISPLAY_CACHE, _DISPLAY_RE, _cache_lookup
from tools.mobile.adb_host import host

logger = logging.getLogger("tools.mobile.adb")


_REFUSED_SIZE = "refusing an implausible display size %dx%d from %s"


def _wm_lines(result: dict) -> list[str]:
    """Stripped, lower-cased stdout lines of a ``wm`` call; none on an error."""
    if result.get("error"):
        return []
    text = str((result.get("content") or {}).get("out") or "")
    return [line.strip().lower() for line in text.splitlines()]


# NOTES for `display_size`'s guards (kept here so the function stays short).
#
# The ceiling is ONE clause over the longer edge, not two comparisons joined by
# `or`. A two-clause version can be written halfway -- drop the height half and
# a tall absurd panel walks through -- and it would collide with the zero-guard
# in the clause ratchet's locator, which matches on the names a condition
# mentions. `max` cannot be half-applied. The regex bounds the digit COUNT, not
# the value: 9999999x9999999 parses as a plausible panel, so it is refused with
# `continue`, meaning "no answer", which every consumer already handles; the
# dump's own bounds stay the authority for targeting either way.
#
# An override REPLACES the physical size and is what is being drawn to, so it
# wins on its own merit rather than by being printed second. AOSP prints
# physical first, but "the last line wins" would silently take the wrong one on
# a build that does not -- and a report scaled to the panel while the device
# draws an override is at the wrong scale for the whole run.
# ``wm size`` prints ``Physical size: 1080x2400`` and, where the display has
# been resized, an additional ``Override size:`` line. The override is what is
# actually being drawn to, so it wins where both appear.
#
# This exists because **the display rectangle is not in a uiautomator dump**.
# Four rounds of `perception._select_viewport` tried to infer it from window
# geometry and each fixed the previous round's fix; docs/DECISIONS.md ->
# *The display size is not in the dump* has the measured record. It is a
# device fact, so it is read from the device.
#
# Never raises and never refuses loudly: a device that cannot answer gets
# ``{"error": None, "content": None}``, and `prune` falls back to a frame
# derived from the dump's own windows. A missing display size must degrade the
# report's scale, never lose the tester's screen.
async def display_size(serial: str) -> dict:
    """The device's NATURAL display size as ``[w, h]``, or ``None`` content."""
    key = str(serial or "")
    now = host().time.monotonic()
    hit, cached = _cache_lookup(_DISPLAY_CACHE, key, now)
    if hit:
        return {"error": None, "content": cached}
    size = _pick_display_size(
        _wm_lines(await host().shell(key, ["wm", "size"])), serial
    )
    _DISPLAY_CACHE[key] = (now + host().DISPLAY_CACHE_TTL_S, size)
    return {"error": None, "content": size}


def _wm_size_candidate(lowered: str, serial: str) -> tuple[list[int], bool] | None:
    """``([w, h], is_override)`` for one ``wm size`` line, or ``None`` to skip it."""
    override = lowered.startswith("override size:")
    if not override and not lowered.startswith("physical size:"):
        return None
    match = _DISPLAY_RE.search(lowered)
    if not match:
        return None
    width, height = int(match.group(1)), int(match.group(2))
    if width <= 0 or height <= 0:
        return None
    # See the NOTES above `display_size` for the ceiling and the precedence.
    if max(width, height) > host().MAX_DISPLAY_EDGE_PX:
        logger.debug(_REFUSED_SIZE, width, height, serial)
        return None
    return [width, height], override


def _pick_display_size(lines: list[str], serial: str) -> list[int] | None:
    """The size the ``wm size`` lines agree on; an override beats a physical line."""
    size = None
    overridden = False
    for lowered in lines:
        candidate = _wm_size_candidate(lowered, serial)
        if candidate is None:
            continue
        found, override = candidate
        if overridden and not override:
            continue
        size = found
        overridden = overridden or override
    return size
