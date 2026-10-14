"""Ending a watch: the capture stop, the summary file and the tidy-up.

``capture.finish_network`` is looked up on ``capture`` at call time, so a test
that patches it bites here.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path

from config.settings_guard import never_raise
from tools.mobile import paths
from tools.mobile import paths as _paths
from tools.mobile_evidence import capture, capture_gate
from tools.mobile_evidence.network_watch_key import (
    WATCH_RUN_PREFIX,
    _key,
    _under,
)
from tools.mobile_evidence.network_watch_state import _WATCHES, _now

logger = logging.getLogger("tools.mobile_evidence.network_watch")


def watch_dir(serial: str) -> Path:
    """Where a watch's summary lands: beside the lane's cache, not inside a run."""

    return paths.sub("watch") / (_key(serial) or "unknown")


def _write_summary(serial: str, record: dict) -> str:
    """Write the summary under the watch directory; its path, or "" on any fault."""
    written = ""
    with never_raise(
        logger,
        "network_watch: could not write the watch summary: %s",
        level=logging.INFO,
        with_exc=True,
    ):
        target_dir = watch_dir(serial)
        if not _under(target_dir, _paths.sub("watch")):
            raise ValueError("watch dir escaped the watch root: %s" % target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / ("watch-%d.json" % int(time.time()))
        target.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
        written = str(target)
    return written


def _fabricated(run_id: str) -> bool:
    # The guard is the load-bearing line: only a directory whose id this
    # module fabricated can be removed here. A run id that came from
    # anywhere else must never reach an rmtree.
    tail = str(run_id)[len(WATCH_RUN_PREFIX) + 1 :]
    return str(run_id).startswith(WATCH_RUN_PREFIX + "-") and bool(_key(tail))


def _remove_fabricated(run_id: str) -> None:
    """Remove the run directory a watch fabricated; never anything else."""
    with never_raise(
        logger,
        "network_watch: could not remove the fabricated run dir: %s",
        level=logging.INFO,
        with_exc=True,
    ):
        if _fabricated(run_id):
            run_path = paths.run_dir(str(run_id))
            # Prefix AND shape AND containment: the id was fabricated here, its
            # serial part is a plain token, and the directory is inside the run
            # store. The prefix alone was shown insufficient by execution.
            if run_path.is_dir() and _under(run_path, paths.sub("runs")):
                shutil.rmtree(run_path, ignore_errors=True)


# Move the summary out of the run store, and remove the fabricated run.
#
# Returns the path written, or "". Never raises: a watch that cannot tidy up
# is still a watch that must return its rows.
def _relocate(run_id: str, serial: str, record: dict) -> str:
    """Move the summary out of the run store, and remove the fabricated run."""
    written = _write_summary(serial, record)
    _remove_fabricated(run_id)
    return written


async def _finish(key: str, live: dict) -> dict:

    args = (key, live["package"], live["run_id"], "watch", 0, live["begun"])
    return await capture.finish_network(capture.FinishReq(*args))


def _stopped(key: str, live: dict, finished: dict) -> dict:
    record = finished.get("content") or {}
    rows = list(record.get("rows") or record.get("hosts") or [])
    summary_path = _relocate(live["run_id"], key, record)
    return {
        "error": finished.get("error"),
        "content": {
            "serial": key,
            "state": "stopped",
            "started_at": live["started_at"],
            "stopped_at": _now(),
            "rows": rows,
            "summary_path": summary_path,
            "note": record.get("skipped") or record.get("note") or "",
        },
    }


async def stop(serial: str) -> dict:
    """End a watch and return its parsed rows. Never needs ``apply``."""

    refusal = capture_gate.gate_refusal()
    if refusal:
        return {"error": refusal, "content": None}
    key = _key(serial)
    live = _WATCHES.pop(key, None)
    if live is None:
        return {
            "error": None,
            "content": {"serial": key, "state": "not watching", "rows": []},
        }
    return _stopped(key, live, await _finish(key, live))
