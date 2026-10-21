"""The newest case-checkpoint time of a run: the idle release's persisted clock.

A leaf (run_store only) so the busy reply (render_busy) can read it without
importing session_finish_ports, whose case_runner import closes an import cycle.
"""

from __future__ import annotations

from tools.mobile import run_store


def last_checkpoint_ts(run_id: str) -> float | None:
    """When the newest case checkpoint of *run_id* was written, or None.

    The idle release's persisted clock for a holder this process no longer
    tracks (evicted from the reaper table, or the server restarted). Never the
    lease stamp: the heartbeat refreshes that for as long as the run lives.
    Never raises.
    """
    try:
        cases = run_store.run_path(run_id) / run_store.CASES_DIR
        stamps = [p.stat().st_mtime for p in cases.glob("TC-*.json")]
    except (OSError, ValueError):
        return None
    return max(stamps, default=None)
