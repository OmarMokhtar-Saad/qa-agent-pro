"""Learned wait ceilings (B16): a condition wait's bound from its own history.

The bound is ``max(2 s, 3 x p95)`` of the app's earlier OK waits on this
(screen, wait op); it only ever TIGHTENS the declared ``max_s`` (the script
budget stays intact) and is the declared bound while fewer than
``MIN_SAMPLES_WAIT`` samples exist. A wait that met its condition after more
than ``2 x p95`` is flagged "slower than usual" with the host's options.
Pure: samples come from ``ctx.knowledge["snap"]["waits"]``, zero I/O.
"""

from __future__ import annotations

import math

from tools.mobile import knowledge_limits

FLOOR_MS = 2000
CEILING_X = 3
SLOW_X = 2
_P95 = 0.95
OPTIONS = (
    "options: keep waiting with wait_until_changed, act on what is on screen, "
    "or hand the step to the tester with ask_tester"
)


def p95(values: list) -> int:
    """Nearest-rank 95th percentile of *values* (ms); 0 for none."""
    ordered = sorted(int(v) for v in values)
    if not ordered:
        return 0
    return ordered[max(0, math.ceil(_P95 * len(ordered)) - 1)]


def known(samples: list) -> bool:
    """True when *samples* are enough to trust (``MIN_SAMPLES_WAIT``)."""
    return len(samples) >= knowledge_limits.MIN_SAMPLES_WAIT


def ceiling_ms(samples: list, declared_ms: int) -> int:
    """The bound for a wait declared at *declared_ms*: learned when enough samples."""
    if not known(samples) or declared_ms <= 0:
        return declared_ms
    return min(declared_ms, max(FLOOR_MS, CEILING_X * p95(samples)))


def slow_note(elapsed_ms: int, samples: list) -> str:
    """``"slower than usual ..."`` when a met wait took over 2 x p95, else ``""``."""
    usual = p95(samples) if known(samples) else 0
    if usual <= 0 or elapsed_ms <= SLOW_X * usual:
        return ""
    return "slower than usual: waited %.1f s, usually up to %.1f s; %s" % (
        elapsed_ms / 1000,
        usual / 1000,
        OPTIONS,
    )
