"""The server's own home-screen check at the start of a run."""

from __future__ import annotations

from handlers.mobile import run_skipped
from tools.mobile import home_preflight, prerun_timing
from tools.mobile.goal_home_start import home_check_is_preflight


async def check_first(serial: str, goal: str) -> None:
    """When the goal's home check comes before its app steps, do it now and
    record what was seen; the reply then asks the host for no Home press.
    A failed press records nothing, so the host still presses Home itself."""
    if serial and home_check_is_preflight(goal):
        seen = await home_preflight.press_home_and_observe(serial)
        if seen != home_preflight.PRESS_FAILED:
            run_skipped.mark_home_checked(serial, seen)


async def begin_stage(serial: str, goal: str) -> None:
    """Start a run's app stage clean (skips and pre-run rows of an earlier
    start forgotten), then run the home check under the ``home_preflight`` row."""
    run_skipped.forget_run(serial)
    prerun_timing.forget(serial)
    await prerun_timing.timed(serial, "home_preflight", check_first(serial, goal))
