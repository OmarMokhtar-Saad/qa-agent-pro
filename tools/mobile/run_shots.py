"""Screen frames: ``runs/<run_id>/shots/<observation_id>.png``.

Store the PNG of ONE OBSERVATION. Never raises.

*obs_id* is what :func:`observation_key` minted -- a look at a screen, not a
screen. THAT IS THE WHOLE POINT OF THIS FUNCTION'S KEY: ``_screen_id``
hashes ``package|activity|texts[:3]``, and on a chat screen the top three
texts are the toolbar, so four turns of a conversation share one screen id.
Measured through the producer's own fixtures
(``tests/mobile/observation_matrix``): four chat turns give ONE screen id and
FOUR observation keys, while a three-step settings walk gives one of each.
Keying here on the screen id would keep the last turn and overwrite three,
the exact defect a0217ab9 fixed for the wireframes.

No key is minted here. The caller asks :func:`observation_id`, the one
producer, so the writer cannot store a key a reader would not ask for.

Idempotent: the same look re-observed rewrites one file, which is why
walking away from a screen and back costs one frame and not two.

A refusal is CONTENT for the caller to ignore -- a lost picture may never
cost a tester the packet, the same trade :func:`write_screen` makes.

The bytes go in RAW. :func:`redact` cannot mask pixels; see the module
docstring.

The stored PNG for one observation, or ``content=None``. Never raises.

``None`` rather than empty bytes, for the reason above: the report renders
an ``<img>`` only from a non-empty answer here, so a capture that never
happened cannot leave the page claiming a picture exists.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from tools.mobile import run_consts, run_io

logger = logging.getLogger("tools.mobile.run_store")


def _bad_frame(ident: str, data: object) -> str | None:
    if not run_consts._RUN_ID_RE.match(ident):
        return (
            "Refusing "
            + repr(ident[:40])
            + " as an observation id; ask observation_key for one."
        )
    if not isinstance(data, (bytes, bytearray)) or not data:
        # "There is no picture" and "here is a picture of nothing" are
        # different answers, and only the first is true of empty bytes.
        return "No image bytes to store."
    return None


def _cap_refusal(run_id: str, target: Path) -> str | None:
    if target.exists():
        return None
    directory = target.parent
    kept = len(list(directory.glob("*.png"))) if directory.is_dir() else 0
    if kept < run_consts.MAX_RUN_SHOTS:
        return None
    logger.warning(
        "mobile.run_store: run %s already keeps %d screen frames; "
        "this one is not stored",
        run_id,
        kept,
    )
    return "This run already keeps its limit of frames."


def _shot_refusal(run_id: str, target: Path, frame: tuple[str, object]) -> str | None:
    """Why :func:`write_shot` must not store this frame, or ``None`` to store it."""
    return _bad_frame(*frame) or _cap_refusal(run_id, target)


def write_shot(run_id: str, obs_id: str, data: Any) -> dict:
    """Store the PNG of ONE OBSERVATION. Never raises."""
    try:
        if not run_io.valid_run_id(run_id):
            return {"error": "Invalid run id.", "content": None}
        ident = str(obs_id or "")
        target = run_io.run_path(run_id) / run_consts.SHOTS_DIR / (ident + ".png")
        refusal = _shot_refusal(run_id, target, (ident, data))
        if refusal:
            return {"error": refusal, "content": None}
        payload = bytes(data)
        run_io._write_bytes(target, payload)
        return {
            "error": None,
            "content": {"observation_id": ident, "bytes": len(payload)},
        }
    except Exception as exc:
        logger.exception("mobile.run_store.write_shot failed")
        return {"error": str(exc), "content": None}


def read_shot(run_id: str, obs_id: str) -> dict:
    """The stored PNG for one observation, or ``content=None``. Never raises."""
    try:
        if not run_io.valid_run_id(run_id):
            return {"error": "Invalid run id.", "content": None}
        ident = str(obs_id or "")
        if not run_consts._RUN_ID_RE.match(ident):
            return {"error": "Invalid observation id.", "content": None}
        target = run_io.run_path(run_id) / run_consts.SHOTS_DIR / (ident + ".png")
        if not target.is_file():
            return {"error": None, "content": None}
        data = target.read_bytes()
        return {"error": None, "content": data or None}
    except Exception as exc:
        logger.exception("mobile.run_store.read_shot failed")
        return {"error": str(exc), "content": None}
