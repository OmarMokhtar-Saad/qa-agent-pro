"""Screen observations: ``runs/<run_id>/observations/<observation_id>.json``.

The id of ONE observation: ``<screen_id>-<12 hex of the content hash>``.

THE one producer of this string. The writer here mints it and every reader
that joins a trace entry to a stored screen asks for it here, so a reader
cannot mint a key the writer would not have written.

A screen with no content hash -- a record written before observations
existed, a hand-built fixture -- is its own single observation, so the key
degrades to the bare screen id and every existing reader behaves exactly as
it did before.

:func:`observation_key` for a PRUNED screen, or ``""``.

Keep this look at the screen beside the canonical one. Never raises.

Returns the observation id, or ``""`` when there was nothing distinct to
keep, when the run has reached :data:`MAX_RUN_OBSERVATIONS`, or when the
write failed. Each of those is CONTENT for a caller that must ignore it:
the canonical screen is already on disk by the time this runs, and a lost
wireframe may never cost a case its verdict.

The cap is counted over the DIRECTORY rather than tracked in the manifest.
A run resumed from another chat has no in-memory count, and a cap that
resets on resume is not a cap.

``{observation_id: screen}`` -- what a STEP's evidence joins against.

Separate from :func:`list_screens` on purpose. That one answers which
screens a run visited, one entry per screen, and is what the accessibility
audit iterates; this one answers what was on them each time a step looked.
A reader that wants "was this screen usable" wants the first; a reader that
wants "what did the model see when it chose that action" wants this one.
"""

from __future__ import annotations

import logging
from pathlib import Path

from tools.mobile import run_consts, run_io

logger = logging.getLogger("tools.mobile.run_store")


def observation_key(screen_id: object, screen_hash: object) -> str:
    """The id of ONE observation: ``<screen_id>-<12 hex of the content hash>``."""
    ident = str(screen_id or "")
    if not run_consts._RUN_ID_RE.match(ident):
        return ""
    digest = "".join(
        char for char in str(screen_hash or "").lower() if char in "0123456789abcdef"
    )[:12]
    return (ident + "-" + digest) if digest else ident


def observation_id(screen: object) -> str:
    """:func:`observation_key` for a PRUNED screen, or ``""``."""
    body = screen if isinstance(screen, dict) else {}
    return observation_key(body.get("screen_id"), body.get("hash"))


def _at_cap(run_id: str, directory: Path, target: Path) -> bool:
    if target.exists():
        return False
    kept = len(list(directory.glob("*.json"))) if directory.is_dir() else 0
    if kept < run_consts.MAX_RUN_OBSERVATIONS:
        return False
    logger.warning(
        "mobile.run_store: run %s already keeps %d screen "
        "observations; this one is not stored",
        run_id,
        kept,
    )
    return True


def _write_observation(run_id: str, screen_id: str, body: dict) -> str:
    """Keep this look at the screen beside the canonical one. Never raises."""
    try:
        ident = observation_key(screen_id, body.get("hash"))
        if not ident or ident == screen_id:
            return ""
        directory = run_io.run_path(run_id) / run_consts.OBSERVATIONS_DIR
        target = directory / (ident + ".json")
        if _at_cap(run_id, directory, target):
            return ""
        run_io._write_json(target, body)
        return ident
    except Exception:
        logger.exception("mobile.run_store._write_observation failed")
        return ""


def _read_observations(directory: Path) -> dict:
    out: dict = {}
    if directory.is_dir():
        for child in sorted(directory.glob("*.json")):
            body = run_io._read_json(child)
            if not isinstance(body, dict):
                continue
            key = observation_key(body.get("screen_id"), body.get("hash"))
            if key:
                out[key] = body
    return out


def list_observations(run_id: str) -> dict:
    """``{observation_id: screen}`` -- what a STEP's evidence joins against."""
    try:
        if not run_io.valid_run_id(run_id):
            return {"error": "Invalid run id.", "content": None}
        directory = run_io.run_path(run_id) / run_consts.OBSERVATIONS_DIR
        return {"error": None, "content": _read_observations(directory)}
    except Exception as exc:
        logger.exception("mobile.run_store.list_observations failed")
        return {"error": str(exc), "content": None}
