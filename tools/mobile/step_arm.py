"""Who arms the step ledger: the outer handler, or the inner step when alone.

``handle_submit_mobile_step_content`` plans flows, folds findings and saves the
note AROUND the step; armed only inside the step, that work sat outside the
ledger. The outer handler arms it first (``begin_outer``) and the inner step
then keeps that start (``begin_inner``) instead of re-arming; a step called
directly arms itself as before.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar

from tools.mobile import call_stamps, prerun_timing, step_timing

logger = logging.getLogger("tools.mobile.step")

_OUTER: ContextVar = ContextVar("_STEP_OUTER", default=False)


def _arm() -> None:
    """Arm the ledger and ledger the pre-run phases (``prerun:*`` rows)."""
    step_timing.begin()
    prerun_timing.replay_all_into_ledger()


def begin_outer() -> None:
    """Arm the ledger from the outermost handler."""
    call_stamps.clear()
    call_stamps.received()
    _arm()
    _OUTER.set(True)


def begin_inner() -> None:
    """Arm for the step itself, unless an outer handler already did."""
    if not _OUTER.get():
        _arm()


def _log_edges() -> None:
    """B12: log receive/reply edges and the call time the manifest timer did not count."""
    edges = call_stamps.reply_sent()
    if edges:
        manifest = step_timing.summary().get("total_ms", 0)
        outside = call_stamps.outside_ms(edges["server_ms"], manifest)
        logger.info("mobile.step stamps %s outside_manifest_ms=%d", edges, outside)


def end_outer() -> None:
    """The outer handler is done: the next direct step arms itself."""
    _log_edges()
    _OUTER.set(False)
