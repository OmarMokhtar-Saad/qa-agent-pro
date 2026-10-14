"""Replay engine: the run Context, status constants, per-run state classes and the sleep seam. Cut verbatim from tools/mobile/executor.py (P4.5)."""

from __future__ import annotations

from tools.mobile.replay.context_consts import (  # noqa: F401
    AUTO_SETTLE_MAX_S,
    DEFAULT_WAIT_UNTIL_TEXT_S,
    DIALOG_SETTLE_S,
    LEGACY_WAIT_MAX_MS,
    MAX_DIALOG_TAPS,
    MAX_TRACE,
    NO_AUTO_SETTLE_OPS,
    STATUS_DONE,
    STATUS_ERROR,
    STATUS_NEEDS_MODEL,
    STATUS_NEEDS_TESTER,
    STATUS_UNVERIFIED,
    SUBMIT_BUDGET_S,
    WAIT_POLL_S,
    logger,
)
from tools.mobile.replay.context_ctx import (  # noqa: F401
    Context,
)
from tools.mobile.replay.context_state import (  # noqa: F401
    ReplayStop,
    Splice,
    _append,
    _ReplayRun,
    _ReplayState,
    _result,
    _sleep,
    _Step,
    _StepCursor,
)
