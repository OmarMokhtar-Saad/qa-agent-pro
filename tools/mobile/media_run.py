"""The run-long recording: one device recorder per run, in rotating segments.

The step clips (:mod:`tools.mobile.media_clip`) only cover replayed steps, so
their join is seconds long for a run of minutes. This module records the WHOLE
run from its first step: ``screenrecord`` stops under the device's 180 s
limit (:data:`MAX_RUN_SEGMENT_SECONDS`), and when a segment ends its file is
pulled into the run's media folder as ``run-seg-NNN.mp4`` and the next segment
starts. ``rmed_run`` joins the segments into the report's one video.

A credential must never be on film. A step whose clip is withheld closes the
running segment (kept: it ends before the typing starts) and records NOTHING
until the step finishes or a normal step starts; then a new segment starts. A
segment that does not stop in time is deleted on the device, never pulled.

Every stop targets the recorder's own pid (``adb_media._record_cmd``), so the
step clips and this recorder run side by side on one device. A rotation only
happens while the run still holds the device: a run that ended on a path that
never called :func:`stop` records at most one more segment. Nothing here
raises: a picture is not a verdict.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from config.settings_guard import never_raise
from tools.mobile import adb, locks
from tools.mobile.media_args import ClipRef
from tools.mobile.media_consts import (
    MAX_RESPAWN_FAILURES,
    MAX_RUN_SEGMENT_SECONDS,
    MAX_RUN_SEGMENTS,
    MAX_SEGMENT_BYTES,
    MAX_STOP_WAIT_S,
    REMOTE_DIR,
    _safe,
    media_dir,
)
from tools.mobile.media_stop import delete_on_device, kill_recorder, signal_recorder

logger = logging.getLogger("tools.mobile.media")

#: File-name prefix of a pulled segment in the run's media folder.
SEGMENT_PREFIX = "run-seg-"


@dataclass
class _Run:
    """One run's recorder: the live segment and what the next one needs."""

    run_id: str
    serial: str
    seq: int = 0
    secret: bool = False
    failures: int = 0
    live: dict | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    tasks: set = field(default_factory=set)


#: run id -> its recorder. The server is one long-lived process, so a run's
#: recorder lives across the tool calls of that run.
_RUNS: dict[str, _Run] = {}


def segment_name(seq: int) -> str:
    """The local file name of segment *seq*."""
    return SEGMENT_PREFIX + "%03d.mp4" % int(seq)


def _remote(run: _Run) -> str:
    """Where segment ``run.seq`` lives on the device while it is recorded."""
    return "%s/qa-agents-run-%s-%d.mp4" % (REMOTE_DIR, _safe(run.run_id), run.seq)


async def _spawn(run: _Run) -> None:
    """Start the next segment, unless secret, a cap or failures say stop."""
    if run.secret or run.seq >= MAX_RUN_SEGMENTS:
        return
    if run.failures > MAX_RESPAWN_FAILURES:
        return
    remote = _remote(run)
    started = await adb.screenrecord_spawn(run.serial, remote, MAX_RUN_SEGMENT_SECONDS)
    body = started.get("content") or {}
    if started.get("error") or not body.get("proc"):
        run.failures += 1
        return
    live = {"proc": body["proc"], "pid": body.get("pid"), "remote": remote}
    live.update(seq=run.seq, secret=False, exited=asyncio.Event())
    run.seq += 1
    run.live = live
    task = asyncio.ensure_future(_watch(run, live))
    run.tasks.add(task)
    task.add_done_callback(run.tasks.discard)


async def _watch(run: _Run, live: dict) -> None:
    """Wait for the segment to end, keep or delete it, then roll on if due."""
    with never_raise(logger, "mobile.media_run watch failed: %s", with_exc=True):
        await live["proc"].communicate()
        live["exited"].set()
        run.failures = run.failures + 1 if live["proc"].returncode else 0
        await _close(run, live)
        async with run.lock:
            if run.live is live and locks.names_held_by(run.run_id):
                run.live = None
                await _spawn(run)
    live["exited"].set()


async def _close(run: _Run, live: dict) -> None:
    """Pull an ended segment into the media folder (a secret one never)."""
    remote = str(live["remote"])
    if not live["secret"]:
        directory = media_dir(run.run_id)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / segment_name(live["seq"])
        await adb.pull(run.serial, remote, str(target))
        size = target.stat().st_size if target.is_file() else 0
        if not size or size > MAX_SEGMENT_BYTES:
            target.unlink(missing_ok=True)
    await delete_on_device(run.serial, remote)


async def _end_live(run: _Run) -> None:
    """Interrupt the live segment and wait (bounded) for it to stop recording.

    A segment that does not stop in time is marked secret: it may hold the
    frames of the step that follows, so it is deleted rather than kept.
    """
    live, run.live = run.live, None
    if live is None:
        return
    await signal_recorder(run.serial, live.get("pid"))
    with never_raise():
        await asyncio.wait_for(live["exited"].wait(), MAX_STOP_WAIT_S)
        return
    live["secret"] = True
    with never_raise():
        await kill_recorder(run.serial, live.get("pid"))
    with never_raise():
        live["proc"].kill()


async def _switch(run: _Run, secret: bool) -> None:
    """Close the live segment; start the next one unless now secret."""
    async with run.lock:
        await _end_live(run)
        run.secret = secret
        await _spawn(run)


async def follow(ref: ClipRef, secret: bool) -> None:
    """Record this step: start the run's recorder, or switch it to/from secret."""
    with never_raise(logger, "mobile.media_run follow failed: %s", with_exc=True):
        run = _RUNS.get(ref.run_id)
        if run is None:
            run = _RUNS[ref.run_id] = _Run(ref.run_id, ref.serial, secret=secret)
            async with run.lock:
                await _spawn(run)
        elif run.secret != secret:
            await _switch(run, secret)


async def reveal(run_id: str) -> None:
    """A withheld step finished: record on."""
    with never_raise(logger, "mobile.media_run reveal failed: %s", with_exc=True):
        run = _RUNS.get(run_id)
        if run is not None and run.secret:
            await _switch(run, False)


async def stop(run_id: str) -> None:
    """End the run's recording and wait for its last pulls. Idempotent."""
    with never_raise(logger, "mobile.media_run stop failed: %s", with_exc=True):
        run = _RUNS.pop(str(run_id), None)
        if run is None:
            return
        async with run.lock:
            await _end_live(run)
        if run.tasks:
            await asyncio.wait(set(run.tasks))
