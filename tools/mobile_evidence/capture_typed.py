"""Typed values per run, in memory only, for the run-end pull (plan D4).

The memory is bounded and last-touch ordered; ``pull_events`` releases a run's
values on every exit through :func:`forget_run`. Nothing here is written anywhere.
"""

from __future__ import annotations

import logging

from tools.mobile_evidence import capture_consts as _consts
from tools.mobile_shared import scrub

logger = logging.getLogger("tools.mobile_evidence.capture")

#: Typed values per run, IN MEMORY ONLY. The lane types a credential in one call and
#: pulls the app's event log at run end, in another call; a value the app echoed into
#: that log can only be masked if the pull still knows it. Never written anywhere.
_TYPED_BY_RUN: dict[str, set[str]] = {}


def _evict_oldest() -> None:
    evicted = next(iter(_TYPED_BY_RUN))
    _TYPED_BY_RUN.pop(evicted, None)
    logger.warning(
        "mobile_evidence.capture: typed-value memory full; run %s forgotten "
        "before its pull, so its typed values will not be masked from memory",
        evicted,
    )


def remember_typed(run_id: str, values) -> int:
    """Keep this call's typed values for the run-end pull. Returns the set size."""
    key = str(run_id)
    # LAST-TOUCH order: a run that is still typing is moved to the back, so the
    # victim of the bound is the run nobody has driven for longest, never an
    # active one. The eviction is logged by run id -- a disarmed mask must not
    # be silent.
    bucket = _TYPED_BY_RUN.pop(key, None)
    if bucket is None:
        bucket = set()
        while len(_TYPED_BY_RUN) >= _consts.MAX_REMEMBERED_RUNS:
            _evict_oldest()
    _TYPED_BY_RUN[key] = bucket
    for value in values or ():
        text = str(value or "").strip()
        if len(text) >= scrub.MIN_SENSITIVE_LEN:
            bucket.add(text)
    return len(bucket)


def forget_run(run_id: str) -> None:
    """Drop a run's remembered typed values.

    Called from ``pull_events``' ``finally``, so it runs on EVERY exit of the
    pull -- refusal, no profile, run-as refused, exception -- not only after a
    successful one. The pull is the run's end either way.
    """
    _TYPED_BY_RUN.pop(str(run_id), None)
