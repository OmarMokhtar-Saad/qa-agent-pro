"""Who arms the step ledger: the outer handler, or the inner step when alone.

``handle_submit_mobile_step_content`` plans flows, folds findings and saves the
note AROUND the step; armed only inside the step, that work sat outside the
ledger. The outer handler arms it first (``begin_outer``) and the inner step
then keeps that start (``begin_inner``) instead of re-arming; a step called
directly arms itself as before.
"""

from __future__ import annotations

from contextvars import ContextVar

from tools.mobile import prerun_timing, step_timing

_OUTER: ContextVar = ContextVar("_STEP_OUTER", default=False)


def _arm() -> None:
    """Arm the ledger and ledger the pre-run phases (``prerun:*`` rows)."""
    step_timing.begin()
    prerun_timing.replay_all_into_ledger()


def begin_outer() -> None:
    """Arm the ledger from the outermost handler."""
    _arm()
    _OUTER.set(True)


def begin_inner() -> None:
    """Arm for the step itself, unless an outer handler already did."""
    if not _OUTER.get():
        _arm()


def end_outer() -> None:
    """The outer handler is done: the next direct step arms itself."""
    _OUTER.set(False)
