"""Scale a deadline to how slow THIS device really is.

A fixed deadline cannot fit every device: a Pixel dumps its screen in a few
hundred ms, a loaded emulator took 11 s, so a wait cut at a fixed bound timed
out on every poll without ever reading the screen. ``dump_latency`` keeps the
device's measured p90; this module turns it into a deadline that leaves a dump
room to finish, and never into an unbounded one.

Pure arithmetic, no I/O. With no samples (``p90_ms`` is None) or a fast device
every function returns the base value unchanged, so default behaviour is what
it was before this module existed.
"""

from __future__ import annotations

from typing import Optional

#: The most a settle deadline may be stretched, as a multiple of its base.
MAX_SCALE_FACTOR = 4

#: The longest any scaled deadline may become, whatever the p90 says. A device
#: slower than this is reported as slow, not waited on.
MAX_SCALED_TIMEOUT_S = 45.0

#: Base timeout, in seconds, for one read through a tree source.
TREE_TIMEOUT_S = 10.0

#: A deadline is made this much longer than the p90 it must cover.
DUMP_HEADROOM = 1.25

#: Below this p90 (ms) a device is fast enough that nothing is scaled.
SCALE_FROM_P90_MS = 1500


def scale_timeout(
    base_s: float,
    p90_ms: Optional[int],
    *,
    ceiling_s: float = MAX_SCALED_TIMEOUT_S,
) -> float:
    """``base_s``, or the time a dump at this p90 needs, whichever is longer.

    Never below ``base_s`` and never above ``max(base_s, ceiling_s)``.
    """
    base = max(0.0, float(base_s))
    if p90_ms is None or p90_ms < SCALE_FROM_P90_MS:
        return base
    wanted = (float(p90_ms) / 1000.0) * DUMP_HEADROOM
    return max(base, min(wanted, float(ceiling_s)))


def scale_settle(
    base_s: float,
    p90_ms: Optional[int],
    *,
    factor_cap: float = MAX_SCALE_FACTOR,
) -> float:
    """Like :func:`scale_timeout`, but a settle is best-effort, so it may grow
    only to ``factor_cap`` times its base."""
    base = max(0.0, float(base_s))
    stretched = scale_timeout(base, p90_ms)
    return min(stretched, base * max(1.0, float(factor_cap)))
