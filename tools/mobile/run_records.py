"""Per-run JSON record helpers shared by a11y, animations and ime_session.

Callers keep thin wrappers (`_record_path`, `read_record`, ...) because tests
monkeypatch those module attributes; wrappers resolve the callables at CALL time
and pass them in, and run_store.* is always called through the module attribute.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from tools.mobile import run_store

PathFn = Callable[[str], Path]


def record_path(run_id: str, record_file: str) -> Path:
    return run_store.run_path(str(run_id)) / record_file


def read_record(path_fn: PathFn, run_id: str, log: logging.Logger, prefix: str) -> dict:
    try:
        body = run_store._read_json(path_fn(run_id))
    except Exception:
        log.exception("%s.read_record failed", prefix)
        return {}
    return body if isinstance(body, dict) else {}


def write_record(
    path_fn: PathFn, run_id: str, body: dict, log: logging.Logger, prefix: str
) -> dict:
    try:
        run_store._write_json(path_fn(run_id), dict(body or {}))
        return {"error": None, "content": dict(body or {})}
    except Exception as exc:
        log.exception("%s.write_record failed", prefix)
        return {"error": str(exc), "content": None}


def clear_record(
    path_fn: PathFn, run_id: str, log: logging.Logger, prefix: str
) -> None:
    try:
        target = path_fn(run_id)
        if target.is_file():
            target.unlink()
    except Exception:
        log.exception("%s.clear_record failed", prefix)


def stale_records(
    serial: str,
    skip_run_id: str,
    scan_limit: int,
    read_fn: Callable[[str], dict],
    log: logging.Logger,
    prefix: str,
) -> list:
    out: list = []
    try:
        wanted = str(serial or "")
        if not wanted:
            return out
        listed = run_store.list_runs(scan_limit, gc=False) or {}
        for row in listed.get("content") or []:
            run_id = str((row or {}).get("run_id") or "")
            if not run_id or run_id == str(skip_run_id or ""):
                continue
            record = read_fn(run_id)
            if not record or str(record.get("serial") or "") != wanted:
                continue
            out.append({"run_id": run_id, **record})
    except Exception:
        log.exception("%s.stale_records failed", prefix)
    return out
