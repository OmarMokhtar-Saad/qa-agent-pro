"""Per-run JSON record helpers shared by a11y, animations and ime_session.

Callers keep thin wrappers (`_record_path`, `read_record`, ...) because tests
monkeypatch those module attributes; wrappers resolve the callables at CALL time
and pass them in, and run_store.* is always called through the module attribute.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tools.mobile import locks_state, run_io, run_list, run_store

PathFn = Callable[[str], Path]


@dataclass(frozen=True)
class RecordSite:
    path_fn: PathFn
    log: logging.Logger
    prefix: str


@dataclass(frozen=True)
class StaleScan:
    serial: str
    skip_run_id: str
    scan_limit: int
    read_fn: Callable[[str], dict]
    log: logging.Logger
    prefix: str


def record_path(run_id: str, record_file: str) -> Path:
    return run_store.run_path(locks_state.record_segment(run_id)) / record_file


def read_record(site: RecordSite, run_id: str) -> dict:
    try:
        body = run_io.read_json(site.path_fn(run_id))
    except Exception:
        site.log.exception("%s.read_record failed", site.prefix)
        return {}
    return body if isinstance(body, dict) else {}


def write_record(site: RecordSite, run_id: str, body: dict) -> dict:
    try:
        run_io.write_json(site.path_fn(run_id), dict(body or {}))
        return {"error": None, "content": dict(body or {})}
    except Exception as exc:
        site.log.exception("%s.write_record failed", site.prefix)
        return {"error": str(exc), "content": None}


def clear_record(site: RecordSite, run_id: str) -> None:
    try:
        target = site.path_fn(run_id)
        if target.is_file():
            target.unlink()
    except Exception:
        site.log.exception("%s.clear_record failed", site.prefix)
    drop_empty_prerun(run_id)


def _matches(scan: StaleScan, run_id: str) -> dict | None:
    record = scan.read_fn(run_id)
    if not record or str(record.get("serial") or "") != str(scan.serial or ""):
        return None
    return {"run_id": run_id, **record}


def _scan_runs(scan: StaleScan) -> list:
    out: list = []
    listed = run_store.list_runs(scan.scan_limit, gc=False) or {}
    ids = [str((row or {}).get("run_id") or "") for row in listed.get("content") or []]
    mine = locks_state.record_segment(scan.skip_run_id)
    for run_id in ids + run_list.prerun_ids():
        if not run_id or run_id == mine:
            continue
        found = _matches(scan, run_id)
        if found is not None:
            out.append(found)
    return out


def stale_records(scan: StaleScan) -> list:
    out: list = []
    try:
        if not str(scan.serial or ""):
            return out
        out = _scan_runs(scan)
    except Exception:
        scan.log.exception("%s.stale_records failed", scan.prefix)
    return out


def move_record(site: RecordSite, owner: str, run_id: str) -> bool:
    """Move *owner*'s record to *run_id*. ``False`` when there was none, or the
    copy was not written (then the owner keeps it, so a restore still finds it)."""
    body = read_record(site, owner)
    if not body:
        return False
    if write_record(site, run_id, body).get("error"):
        site.log.warning(
            "%s: record of %s not handed to %s", site.prefix, owner, run_id
        )
        return False
    clear_record(site, owner)
    return True


def drop_empty_prerun(owner: str) -> None:
    """Remove a pre-run owner's ``runs/prerun-*`` folder once nothing is left in it."""
    segment = locks_state.record_segment(owner)
    if not segment.startswith(locks_state.PRERUN_DIR_PREFIX):
        return
    try:
        folder = run_store.run_path(segment)
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
    except (OSError, ValueError):
        logging.getLogger(__name__).warning("pre-run folder %s not removed", segment)
