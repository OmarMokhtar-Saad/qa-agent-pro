"""Warn when the emulator or the host is heavy enough to make every step slow.

The Air run that motivated this drove an ``s25_ultra`` AVD (1440x3120, ~600
dpi) on an 8-core laptop at load average ~25, with qemu at 167 % CPU. Every
``uiautomator dump`` and ``screencap`` scales with the panel, and every adb
call queues behind an overloaded host -- the lane was not slow, the machine
was. Nothing told the tester.

ADVICE ONLY. This module never refuses a run and never touches a device: the
callers read the display (``adb.display_size``/``adb.display_density``, both
cached per serial) or the AVD's ``config.ini`` and hand the numbers in. What
the model sees is already downscaled to ``screenshot.MAX_LONG_EDGE_PX``, so
the remedy named here is a lighter AVD, not a capture setting.

The thresholds are judgement, not measurement: a 1080x2400 / 420 dpi Pixel
6-class panel is well inside all of them, and the flagship-class panels that
made the Air run crawl are outside.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

#: Longer panel edge above which an AVD counts as heavy. A 1080p Pixel-class
#: panel is 2400; the s25_ultra is 3120. Bounded from above in
#: ``tests/mobile/test_mobile_bounds_upper.py``.
HEAVY_AVD_MAX_LONG_EDGE_PX = 2400

#: Density above which an AVD counts as heavy. Pixel 6 is 420, the s25_ultra
#: ~600. Bounded from above in ``tests/mobile/test_mobile_bounds_upper.py``.
HEAVY_AVD_MAX_DENSITY_DPI = 560

#: One-minute load average per CPU core above which the host counts as
#: overloaded: past 1.0, runnable work is queueing for a core. The Air read
#: ~3.1. Bounded from above in ``tests/mobile/test_mobile_bounds_upper.py``.
HEAVY_HOST_LOAD_PER_CORE_MAX = 1.0

LIGHTER_AVD = (
    "A 1080p Pixel-class AVD (e.g. Pixel 6, 1080×2400, 420 dpi) makes every "
    "screen dump and screenshot cheaper"
)


def host_load() -> float | None:
    """One-minute load average per core, or ``None`` where it is unknowable."""
    try:
        cores = os.cpu_count() or 0
        if cores <= 0:
            return None
        return os.getloadavg()[0] / cores
    except (OSError, AttributeError):  # Windows has no getloadavg
        return None


def reasons(
    width: int = 0,
    height: int = 0,
    density: int | None = None,
    load_per_core: float | None = None,
) -> list[str]:
    """Each threshold crossed, as a short clause. Empty when nothing is heavy."""
    found: list[str] = []
    try:
        if max(int(width or 0), int(height or 0)) > HEAVY_AVD_MAX_LONG_EDGE_PX:
            found.append(
                f"the panel is {int(width)}×{int(height)} "
                f"(over {HEAVY_AVD_MAX_LONG_EDGE_PX} px on the long edge)"
            )
        if density is not None and int(density) > HEAVY_AVD_MAX_DENSITY_DPI:
            found.append(
                f"density is {int(density)} dpi (over {HEAVY_AVD_MAX_DENSITY_DPI})"
            )
        if load_per_core is not None and load_per_core > HEAVY_HOST_LOAD_PER_CORE_MAX:
            found.append(
                f"this machine's load is {load_per_core:.1f} per core "
                f"(over {HEAVY_HOST_LOAD_PER_CORE_MAX:.1f})"
            )
    except (TypeError, ValueError, OverflowError):
        logger.exception("mobile.avd_guard.reasons got a non-number")
    return found


def assess(
    width: int = 0,
    height: int = 0,
    density: int | None = None,
    load_per_core: float | None = None,
) -> str:
    """One warning sentence, or ``""`` when nothing crossed a threshold."""
    found = reasons(width, height, density, load_per_core)
    if not found:
        return ""
    return "Steps will be slow: " + "; ".join(found) + ". " + LIGHTER_AVD + "."
