"""Display density: ``display_density`` (moved from adb.py)."""

from __future__ import annotations

from tools.mobile.adb_display_state import _DENSITY_CACHE, _DENSITY_RE
from tools.mobile.adb_host import host


def _density_line(line: str) -> tuple[int, bool] | None:
    """``(dpi, is_override)`` for one ``wm density`` line, or ``None`` to skip it."""
    lowered = line.strip().lower()
    override = lowered.startswith("override density:")
    if not override and not lowered.startswith("physical density:"):
        return None
    match = _DENSITY_RE.search(lowered)
    if not match:
        return None
    value = int(match.group(1))
    if not (host().MIN_DENSITY_DPI <= value <= host().MAX_DENSITY_DPI):
        return None
    return value, override


def _density_from(result: dict) -> int | None:
    """The density the ``wm density`` reply agrees on; an override beats physical."""
    if result.get("error"):
        return None
    text = str((result.get("content") or {}).get("out") or "")
    dpi = None
    overridden = False
    for line in text.splitlines():
        parsed = _density_line(line)
        if parsed is None:
            continue
        value, override = parsed
        if overridden and not override:
            continue
        dpi = value
        overridden = overridden or override
    return dpi


# ``wm density`` prints ``Physical density: 420`` and, where a density
# override is set, an additional ``Override density: 480``. The override is
# what the framework actually lays out with, so it wins where both appear --
# and it wins on its OWN MERIT rather than by being printed second, exactly as
# `display_size` treats an override size. `tests/mobile/test_mobile_density.py`
# pins the two functions' precedence together, because two copies of one rule
# are two rules that drift.
#
# This exists because **dp is not in a uiautomator dump**. A dump gives pixels;
# every platform minimum a person cares about ("a touch target is 48dp") is in
# dp, and the conversion needs this number. Without it `screen_audit` has to
# ASSUME a density, and an assumed density misflags compliant controls on
# low-density hardware -- so this is read from the device for the same reason
# the display size is.
#
# Never refuses loudly: a device that cannot answer gets
# ``{"error": None, "content": None}`` and the caller falls back to a stated
# assumption. An accessibility finding must never cost a screen.
async def display_density(serial: str) -> dict:
    """The device's display density in DPI, or ``None`` content. Never raises."""
    key = str(serial or "")
    now = host().time.monotonic()
    cached = _DENSITY_CACHE.get(key)
    if cached is not None and now < cached[0]:
        return {"error": None, "content": cached[1]}
    result = await host().shell(key, ["wm", "density"])
    dpi = _density_from(result)
    _DENSITY_CACHE[key] = (now + host().DISPLAY_CACHE_TTL_S, dpi)
    return {"error": None, "content": dpi}
