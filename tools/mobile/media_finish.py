"""Finishing a step: stop the clip, pull it, index the record."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise
from tools.mobile import adb
from tools.mobile.media_args import ClipRef, _PullJob, _Step
from tools.mobile.media_consts import (
    CLIP,
    MAX_CLIP_BYTES,
    MAX_STOP_WAIT_S,
    NOT_ATTEMPTED,
    OK,
    PULL_FAILED,
    TRUNCATED,
    _safe,
    media_dir,
    record,
)
from tools.mobile.media_defer import defer
from tools.mobile.media_stop import _stop_recorder, reap_recorder, signal_recorder
from tools.mobile.media_store import remember

logger = logging.getLogger("tools.mobile.media")


def _base(step: _Step) -> dict:
    """The keys a clip record and its frame record share."""
    return {
        "tc_id": str(step.ref.tc_id),
        "seq": int(step.held.get("seq") or 0),
        "screen_id": str(step.screen_id or ""),
    }


async def _recorded_clip(step: _Step, clipped: dict) -> dict | None:
    """Pull a recorded clip now, or defer it (then there is no record yet)."""
    name = "clip-" + _safe(step.ref.tc_id) + "-" + str(clipped["seq"]) + ".mp4"
    job = _PullJob(step.ref.serial, step.held, step.directory / name, clipped)
    if not step.ref.defer:
        await _finish_step_pull(job)
        return clipped
    await signal_recorder(step.ref.serial)
    defer(step.ref, _pull_deferred(step.ref, job))
    return None


async def _clip_part(step: _Step) -> dict | None:
    """Close out the clip; None when its pull was deferred (stored once done)."""
    serial, held = step.ref.serial, step.held
    state = str(held.get("state") or NOT_ATTEMPTED)
    clipped = record(CLIP, state, **_base(step))
    if state == OK:
        return await _recorded_clip(step, clipped)
    if held.get("proc") is not None:
        await _stop_recorder(serial, held)
        remote = str(held.get("remote") or "")
        await adb.shell(serial, ["rm", "-f", remote], MAX_STOP_WAIT_S)
    return clipped


async def _pull_deferred(ref: ClipRef, job: _PullJob) -> None:
    """Background half of a clip: reap the recorder, pull, then store the record."""
    await _pull_into(job, await reap_recorder(job.held))
    remember(ref.run_id, ref.tc_id, [job.clipped])


# ``content`` is the list of records written. Never raises, and no branch here
# can change a verdict: the caller ignores the result by design.
async def finish_step(ref: ClipRef, handle: object, screen_id: str = "") -> dict:
    """Stop the clip and store its record on the case. No per-step frame (WP-D)."""
    held = handle if isinstance(handle, dict) else {}
    records = []
    with never_raise(
        logger,
        "mobile.media.finish_step failed: %s",
        level=logging.WARNING,
        with_exc=True,
    ):
        directory = media_dir(ref.run_id)
        directory.mkdir(parents=True, exist_ok=True)
        step = _Step(ref, held, directory, screen_id)
        clip = await _clip_part(step)
        records.extend([clip] if clip else [])
    remember(ref.run_id, ref.tc_id, records)
    return {"error": None, "content": records}


def _pull_failed(pulled: dict, size: int) -> bool:
    """True when the pull did not produce a usable file."""
    # A PULL THAT RAN AND FAILED IS NOT AN `error`. ``adb.raw`` sets
    # ``error`` only when adb could not be RUN at all (spawn failure,
    # timeout); a missing remote file comes back error=None with a
    # NON-ZERO rc and an empty stdout -- the same distinction
    # ``adb.devices`` had to learn. Reading ``error`` alone would leave
    # the size check as the only thing that noticed, which is one fact
    # answered in two places.
    body = pulled.get("content") or {}
    rc = int(body.get("rc") or 0) if isinstance(body, dict) else 0
    return bool(pulled.get("error") or rc or not size)


async def _finish_step_pull(job: _PullJob) -> None:
    """Stop the recorder, pull the clip to the target and set the clip record's state."""
    await _pull_into(job, await _stop_recorder(job.serial, job.held))


def _pull_note(seq: object, pulled: dict, size: int) -> str:
    """Step-numbered reason a pull gave no usable file: empty pull vs copy failed."""
    body = pulled.get("content") or {}
    rc = int(body.get("rc") or 0) if isinstance(body, dict) else 0
    if pulled.get("error") or rc:
        return "step %s: copy failed (rc=%s, error=%s)" % (seq, rc, pulled.get("error"))
    if not size:
        return (
            "step %s: empty pull (rc=0, 0 bytes; recorder may not be finalised)" % seq
        )
    return "step %s: clip %d bytes exceeds the %d byte cap" % (
        seq,
        size,
        MAX_CLIP_BYTES,
    )


def _set_pull_failed(clipped: dict, pulled: dict, size: int) -> None:
    clipped["state"] = PULL_FAILED
    clipped["note"] = _pull_note(clipped.get("seq"), pulled, size)
    logger.warning("media pull failed: %s", clipped["note"])


async def _pull_into(job: _PullJob, closed: bool) -> None:
    """Pull the (already stopped) clip to the target and set the clip record's state."""
    serial, held, target, clipped = job
    remote = str(held.get("remote") or "")
    pulled = await adb.pull(serial, remote, str(target))
    await adb.shell(serial, ["rm", "-f", remote], MAX_STOP_WAIT_S)
    size = target.stat().st_size if target.is_file() else 0
    if size > MAX_CLIP_BYTES:
        target.unlink(missing_ok=True)
    if _pull_failed(pulled, size) or size > MAX_CLIP_BYTES:
        _set_pull_failed(clipped, pulled, size)
    else:
        clipped["state"] = OK if closed else TRUNCATED
        clipped["file"] = target.name
        clipped["bytes"] = int(size)
