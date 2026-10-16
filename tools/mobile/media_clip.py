"""Starting a clip: refuse it (credential, per-case cap) or launch the recorder."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise
from tools.mobile import adb
from tools.mobile.media_args import ClipRef
from tools.mobile.media_consts import (
    CLIP,
    MAX_CLIP_SECONDS,
    MAX_CLIPS_PER_CASE,
    NOT_ATTEMPTED,
    OK,
    REFUSED,
    REMOTE_DIR,
    WITHHELD,
    _safe,
)
from tools.mobile.media_defer import open_clips
from tools.mobile.media_store import records_of
from tools.mobile.media_withhold import withholds

logger = logging.getLogger("tools.mobile.media")


def _clip_count(ref: ClipRef) -> int:
    """How many clips this case has stored so far."""
    stored = [r for r in records_of(ref.run_id, ref.tc_id) if r.get("kind") == CLIP]
    return len(stored) + open_clips(ref.run_id, ref.tc_id)


def _refusal(script: object, seq: int) -> str | None:
    """The state that keeps this step from recording, or None to go ahead."""
    if withholds(script):
        return WITHHELD
    return REFUSED if seq >= MAX_CLIPS_PER_CASE else None


def _remote_path(ref: ClipRef, seq: int) -> str:
    """Where the recording lives on the device while it is being made."""
    return (
        REMOTE_DIR
        + "/qa-agents-"
        + _safe(ref.run_id)
        + "-"
        + _safe(ref.tc_id)
        + "-"
        + str(seq)
        + ".mp4"
    )


async def _launch(ref: ClipRef, handle: dict, remote: str) -> None:
    """Spawn the recorder and set the handle's state to what happened."""
    started = await adb.screenrecord_spawn(ref.serial, remote, MAX_CLIP_SECONDS)
    if started.get("error") or not (started.get("content") or {}).get("proc"):
        handle["state"] = REFUSED
        return
    handle["proc"] = started["content"]["proc"]
    handle["remote"] = remote
    handle["state"] = OK


# *script* is whatever ``actions.parse_script`` returned in ``content`` -- a
# ``Script`` model. A handle is ALWAYS returned, with the state that explains
# it, so the caller has exactly one thing to pass on and never a branch of its
# own.
async def start_clip(ref: ClipRef, script: object) -> dict:
    """Begin recording this step. ``content`` is the handle finish_step wants."""
    handle = {"proc": None, "remote": "", "state": NOT_ATTEMPTED, "seq": 0}
    with never_raise(
        logger,
        "mobile.media.start_clip failed: %s",
        level=logging.WARNING,
        with_exc=True,
    ):
        handle["seq"] = seq = _clip_count(ref)
        refused = _refusal(script, seq)
        if refused is None:
            await _launch(ref, handle, _remote_path(ref, seq))
        else:
            handle["state"] = refused
        return {"error": None, "content": handle}
    handle["state"] = REFUSED
    return {"error": None, "content": handle}
