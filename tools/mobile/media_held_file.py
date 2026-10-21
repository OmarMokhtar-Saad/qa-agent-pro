"""``media-held.json``: the media held for case records that do not exist yet.

Mirrors the in-memory ``media_pending`` map into the run dir on every change,
so a server that dies mid-run loses nothing: the startup sweep reloads it and
writes the stub case records. Never raises.
"""

from __future__ import annotations

import logging

from tools.mobile import run_io

logger = logging.getLogger("tools.mobile.media")

HELD_FILE = "media-held.json"


def save(run_id: str, held: dict) -> None:
    """Write ``{tc_id: [records]}`` for *run_id*; an empty map removes the file."""
    try:
        target = run_io.run_path(run_id) / HELD_FILE
        if held:
            run_io.write_json(target, held)
        else:
            target.unlink(missing_ok=True)
    except (OSError, ValueError, TypeError):
        logger.warning("mobile.media: could not persist held media", exc_info=True)


def load(run_id: str) -> dict:
    """The persisted ``{tc_id: [records]}`` of *run_id*, or ``{}``."""
    try:
        body = run_io.read_json(run_io.run_path(run_id) / HELD_FILE)
    except (OSError, ValueError):
        return {}
    return body if isinstance(body, dict) else {}
