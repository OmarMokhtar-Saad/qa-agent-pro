"""Wall-clock port: the one place library code reads the calendar clock.

Call it through the module (``clock.now()``), never ``from tools.clock import
now``. Both functions delegate at call time to the stdlib, so a test may patch
either ``clock.now`` or the global ``time.time``. Monotonic and perf_counter
reads stay direct: they are not wall-clock time.
"""

from __future__ import annotations

import datetime
import time


def now() -> float:
    """Seconds since the epoch, exactly ``time.time()``."""
    return time.time()


def today() -> datetime.date:
    """The local calendar date, exactly ``datetime.date.today()``."""
    return datetime.date.today()
