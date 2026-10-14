"""Run listing: summaries newest first, and the last package used on a device.

``list_runs``:
Run summaries, newest first.

Collects stale runs first and IGNORES the result: a collection that refused
(the lane is off) or failed must never stop a tester listing their runs.
``gc=False`` is for a caller that wants a pure listing.

``last_used_package``:
The most recently touched run's package on *serial*, or "".

Scans :func:`list_runs` (already newest-first) for the first manifest
whose serial matches and whose package is non-empty. Read-only, no gc --
a caller building an error message must never trigger a collection pass.
"""

from __future__ import annotations

import logging
from pathlib import Path

from config.settings_guard import never_raise
from tools.mobile import paths, run_consts, run_gc, run_io

logger = logging.getLogger("tools.mobile.run_store")


def _run_row(child: Path) -> dict:
    manifest = run_io._read_json(child / run_consts.MANIFEST_FILE)
    manifest = manifest if isinstance(manifest, dict) else {}
    try:
        modified = child.stat().st_mtime
    except OSError:
        modified = 0.0
    return {
        "run_id": child.name,
        "created": manifest.get("created", 0),
        "modified": modified,
        "manifest": manifest,
    }


def _run_rows() -> list[dict]:
    root = paths.sub("runs")
    out: list[dict] = []
    if root.is_dir():
        for child in root.iterdir():
            if child.is_dir() and run_io.valid_run_id(child.name):
                out.append(_run_row(child))
    out.sort(key=lambda item: float(item.get("modified") or 0), reverse=True)
    return out


def list_runs(limit: int = 50, *, gc: bool = True) -> dict:
    """Run summaries, newest first. ``gc=False`` is a pure listing."""
    try:
        if gc:
            with never_raise(
                logger,
                "mobile.run_store: stale-run collection skipped",
                level=logging.INFO,
            ):
                run_gc.gc_stale_runs()
        return {"error": None, "content": _run_rows()[: max(1, int(limit))]}
    except Exception as exc:
        logger.exception("mobile.run_store.list_runs failed")
        return {"error": str(exc), "content": None}


def last_used_package(serial: str) -> str:
    """The most recently touched run's package on *serial*, or ""."""
    serial = str(serial or "")
    if not serial:
        return ""
    listed = list_runs(limit=100, gc=False)
    if listed.get("error"):
        return ""
    for row in listed.get("content") or []:
        manifest = row.get("manifest") if isinstance(row, dict) else {}
        manifest = manifest if isinstance(manifest, dict) else {}
        if str(manifest.get("serial") or "") != serial:
            continue
        package = str(manifest.get("package") or "")
        if package:
            return package
    return ""
