"""Device discovery (``list_devices``) and its TTL cache for ``tools.device_manager``."""

from __future__ import annotations

import asyncio
import logging

from tools.device_manager_host import host

logger = logging.getLogger("tools.device_manager")


def _fresh_cache(now: float, refresh: bool) -> dict | None:
    """The cached discovery reply when it is still inside the TTL, else None."""
    cache = host()._discovery_cache
    if refresh or cache is None:
        return None
    cached_at, cached_result = cache
    if (now - cached_at) < host().DISCOVERY_CACHE_TTL_S:
        return dict(cached_result)
    return None


# Reply keys: ``adb_dropped_lines`` is the count of `adb devices` output lines the
# parser did not read. Always present, including on the error path, so no reader
# has to special-case its absence -- the same rule `unusable` follows.
# ``adb_problem`` is one sentence when `adb start-server` / `adb devices -l` timed
# out or failed (B4); None otherwise. Always present, like `unusable`.
def _discovery_reply(android: tuple, others: list[dict], problem: str | None) -> dict:
    """Build the 5-key discovery reply and log how many devices were found."""
    usable_android, unusable_android, dropped_lines = android
    devices = [*usable_android, *others]
    logger.info(
        "device_manager: discovered %d device(s), %d attached but unusable",
        len(devices),
        len(unusable_android),
    )
    return {
        "content": devices,
        "unusable": unusable_android,
        "adb_dropped_lines": dropped_lines,
        "adb_problem": problem,
        "error": None,
    }


# Run every platform's discovery, build the reply and cache it when clean.
async def _list_devices_discover(now: float) -> dict:
    """Discover on every platform and cache the reply when adb was clean."""
    dm = host()
    android, simulators, physical = await asyncio.gather(
        dm.list_android_all(),
        dm.list_ios_simulators(),
        dm.list_ios_physical(),
    )
    # Plain 3-tuples (tests, older callers) carry no problem.
    adb_problem = getattr(android, "problem", None)
    result = _discovery_reply(android, [*simulators, *physical], adb_problem)
    # A failed adb probe is not a result: cached, it would keep telling the
    # tester about a problem they have already fixed.
    if adb_problem is None:
        setattr(dm, "_discovery_cache", (now, dict(result)))
    return result


# Return every attached Android/iOS device, emulator, and simulator.
#
# Shape: ``{"content": [{id, name, platform, kind, state}, ...], "unusable":
# [{..., state, raw_state, remediation}, ...], "error": None}``.
#
# ``content`` MEANS exactly what it always did -- devices this server can
# drive -- and each Android row is additively extended with ``state`` (always
# ``device`` there). ``unusable`` is the new sibling: Android transports that
# are attached but not drivable yet (``unauthorized``, ``offline``, ``no
# permissions``, an unrecognised state). It is informational ONLY and must
# never be offered as a choice; its serial and raw state are device-supplied
# text, so any renderer must wrap them via ``tools.untrusted``. The empty list
# is always present, including on the error path, so no reader has to
# special-case its absence.
# Never raises. A per-platform tool being missing is NOT an error -- that
# platform is simply skipped. Only a truly unexpected failure sets ``error``.
#
# Cached for ``DISCOVERY_CACHE_TTL_S`` seconds (audit item 5): discovery
# forked ``xcrun simctl``/``devicectl`` on every call, which is slow and,
# without full Xcode, fails outright. ``refresh=True`` bypasses the cache.
async def list_devices(refresh: bool = False) -> dict:
    """Return every attached Android/iOS device, emulator, and simulator."""
    now = host().time.monotonic()
    cached = _fresh_cache(now, refresh)
    if cached is not None:
        return cached
    try:
        return await _list_devices_discover(now)
    except Exception as exc:
        logger.exception("device_manager: unexpected error listing devices")
        return {
            "error": str(exc),
            "content": None,
            "unusable": [],
            "adb_dropped_lines": 0,
            "adb_problem": None,
        }
