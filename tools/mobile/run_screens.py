"""Pruned screens: ``runs/<run_id>/screens/<screen_id>.json``.

Store one PRUNED screen under its own ``screen_id``.

Idempotent by design: the id is stable across a one-pixel scroll, so the
same screen re-observed rewrites one file rather than adding another. That
is what the report dedupes on, and it is deliberately blind to content -- so
the same call ALSO keeps this look at the screen as its own observation,
under :func:`observation_key`, which is what a step's evidence joins to. One
producer, two files, two questions. The payload goes through the same
redacting writer as everything else here, both times.

A refusal is CONTENT for the caller to ignore: the writers are mid-run
lifecycle functions, and a report that lost a wireframe is a far lesser
failure than a case that lost its verdict.

``{screen_id: screen}`` -- what the report joins a trace's ids against.

The most recently written pruned screen of the run, or ``{}``.

``write_screen`` rewrites a screen's file on every look at it, so the newest
file by modification time is the screen the last step ended on. Never
raises.
"""

from __future__ import annotations

import logging
from pathlib import Path

from tools.mobile import run_consts, run_io, run_observations

logger = logging.getLogger("tools.mobile.run_store")


def _bad_screen_id(screen_id: str) -> dict:
    return {
        "error": (
            "Refusing "
            + repr(screen_id[:40])
            + " as a screen id; a pruned screen carries a hex screen_id."
        ),
        "content": None,
    }


def _store_screen(run_id: str, screen_id: str, body: dict) -> dict:
    target = run_io.run_path(run_id) / run_consts.SCREENS_DIR / (screen_id + ".json")
    run_io._write_json(target, body)
    observed = run_observations._write_observation(run_id, screen_id, body)
    return {
        "error": None,
        "content": {"screen_id": screen_id, "observation_id": observed},
    }


def write_screen(run_id: str, screen: object) -> dict:
    """Store one PRUNED screen under its own ``screen_id``. Idempotent."""
    try:
        if not run_io.valid_run_id(run_id):
            return {"error": "Invalid run id.", "content": None}
        body = dict(screen) if isinstance(screen, dict) else {}
        screen_id = str(body.get("screen_id") or "")
        if not run_consts._RUN_ID_RE.match(screen_id):
            return _bad_screen_id(screen_id)
        return _store_screen(run_id, screen_id, body)
    except Exception as exc:
        logger.exception("mobile.run_store.write_screen failed")
        return {"error": str(exc), "content": None}


def _read_screens(directory: Path) -> dict:
    out: dict = {}
    if directory.is_dir():
        for child in sorted(directory.glob("*.json")):
            body = run_io._read_json(child)
            if isinstance(body, dict) and body.get("screen_id"):
                out[str(body["screen_id"])] = body
    return out


def list_screens(run_id: str) -> dict:
    """``{screen_id: screen}`` -- what the report joins a trace's ids against."""
    try:
        if not run_io.valid_run_id(run_id):
            return {"error": "Invalid run id.", "content": None}
        directory = run_io.run_path(run_id) / run_consts.SCREENS_DIR
        return {"error": None, "content": _read_screens(directory)}
    except Exception as exc:
        logger.exception("mobile.run_store.list_screens failed")
        return {"error": str(exc), "content": None}


def latest_screen(run_id: str) -> dict:
    """The most recently written pruned screen of the run, or ``{}``. Never raises."""
    try:
        if not run_io.valid_run_id(run_id):
            return {}
        directory = run_io.run_path(run_id) / run_consts.SCREENS_DIR
        if not directory.is_dir():
            return {}
        files = sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime)
        body = run_io._read_json(files[-1]) if files else None
        return body if isinstance(body, dict) else {}
    except OSError:
        logger.exception("mobile.run_store.latest_screen failed")
        return {}
