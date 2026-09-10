"""Pixels of the run: one clip around each replayed step, one frame after it.

The pruned uiautomator dump describes a screen in TEXT, and the report has only
ever drawn that text back as a wireframe. This module stores what the device
actually showed: an mp4 recorded around each replayed script, and a PNG of the
screen that script left behind.

**One clip per SUBMITTED STEP, not per case.** ``actions.SUBMIT_BUDGET_MS`` is
40 seconds, so one replay can never approach the ~180 second ceiling
``screenrecord`` enforces on the device itself. Recording per replay therefore
needs no segment chaining at all, and :data:`MAX_CLIP_SECONDS` is passed as an
explicit ``--time-limit`` so the bound is a number this repository owns, is
swept by ``tests/mobile/test_mobile_bounds_upper.py``, and is unreachable by
construction rather than by hope.

**One producer, one meaning, EVERY consumer.** Every outcome is one of
:data:`STATES`, and each state has exactly one sentence in :data:`NOTES`. The
report renders ``NOTES[state]`` and never invents its own wording, so a state
added here without a consumer is visible in the page instead of falling through
to a generic branch and telling a tester nothing.

**A credential is withheld, not masked.** ``run_store.redact`` masks strings; a
frame is pixels and there is nowhere in a PNG to put a mask. So a step whose
script carries a credential stores NO picture at all: the recorder is stopped
and the file deleted on the device without ever being pulled, no frame is taken,
and the record says ``credential_withheld`` in this module's own words. Both
value-bearing ops count -- see :func:`withholds`.

**The stop path costs WALL CLOCK, and its ceiling is named here.** Stopping a
recorder is an interrupt on the device followed by a wait, and both halves are
bounded by :data:`MAX_STOP_WAIT_S` -- deliberately NOT ``adb.DEFAULT_TIMEOUT_S``
(30 seconds). On any image where ``pkill`` is absent, or where the interrupt
never reaches the recorder, waiting an adb default would cost a tester THIRTY
SECONDS PER STEP for a picture. After the window the adb CLIENT is killed
rather than waited on further: the recording is already unusable at that point,
and the only remaining question is how long the run pays for it. The worst case
is therefore about two times :data:`MAX_STOP_WAIT_S` per step, and it is stated in
the plan's risk table as a number rather than as "bounded by adb's timeout".

**``pkill -INT screenrecord`` is device-GLOBAL, and that is not fixable here.**
There is no pid to target: the recorder is spawned by the shell adb runs, and
its pid is not ours. So this interrupts EVERY ``screenrecord`` on that serial.
What makes it tolerable is the per-device lock in ``tools/mobile/locks.py`` --
one run owns a serial at a time -- so the only other recording that can exist
is one a human started by hand on the same device.

Every public function returns ``{"error", "content"}`` and never raises: a
picture is not a verdict, and nothing here may cost a tester a step.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from tools.mobile import actions as actions_mod
from tools.mobile import adb, paths, run_store

logger = logging.getLogger(__name__)

#: This module's OWN handle on ``asyncio.wait_for``.
#:
#: The stop's timeout is the fact a test must be able to observe, and the honest
#: way to observe it is to replace THIS name -- module-local, and restored by
#: ``monkeypatch`` at the end of the test -- rather than ``asyncio.wait_for``
#: itself. Patching the stdlib attribute reaches every coroutine that awaits a
#: timeout anywhere in the same test process, so a spy meant to read one number
#: silently re-times unrelated code.
_wait_for = asyncio.wait_for

#: The report is a self-contained FOLDER: its index and its assets together.
REPORT_DIRNAME = "report"
MEDIA_DIRNAME = "media"

#: Seconds of one clip, passed to ``screenrecord --time-limit``. One replay is
#: bounded by ``actions.SUBMIT_BUDGET_MS`` (40s), so this sits above what a step
#: can produce and far below what the device would impose.
MAX_CLIP_SECONDS = 60

#: Bytes of one clip that is KEPT. A larger file is deleted rather than stored,
#: because a run folder a tester cannot mail is not evidence.
MAX_CLIP_BYTES = 24 * 1024 * 1024

#: Clips stored for one case. ``actions.MAX_ACTIONS`` bounds one script, and a
#: case cannot submit more steps than the run's own step budget allows.
MAX_CLIPS_PER_CASE = 40

#: Seconds spent STOPPING one recorder: the interrupt call and the wait for the
#: recorder to close its container are each bounded by this, and the adb client
#: is killed once it expires. NOT ``adb.DEFAULT_TIMEOUT_S`` -- a 30-second wait
#: per step is a tester's time spent on a picture, and it is the difference
#: between a bounded cost and an unstated one.
#:
#: The ``MAX_`` prefix is load-bearing, not decoration: ``tests/cap_scanner``
#: decides what IS a cap by splitting the name on ``_`` and intersecting the
#: segments with ``CAP_WORDS``. ``STOP_WAIT_S`` -- the name this constant nearly
#: shipped under -- has no such segment, so the completeness sweep in
#: ``test_mobile_bounds_upper.py`` would never have forced a CEILINGS row for
#: it, and the one cap here that is paid on EVERY step would have been the one
#: cap with no ceiling.
MAX_STOP_WAIT_S = 4

OK = "ok"
TRUNCATED = "truncated"
REFUSED = "refused"
PULL_FAILED = "pull_failed"
WITHHELD = "credential_withheld"
NOT_ATTEMPTED = "not_attempted"

#: One sentence per state, and the ONLY wording any consumer may show. Each is
#: a different fact about the run and only some are worth investigating.
NOTES: dict = {
    OK: "This is the device's own screen, recorded while the step replayed.",
    TRUNCATED: (
        "The recording reached its "
        + str(MAX_CLIP_SECONDS)
        + "-second limit and stops before the step did; what is here is real, "
        "and the end of the step is missing."
    ),
    REFUSED: (
        "No recording: the device refused to record this step (an emulator "
        "without the recorder, a secure window, or a screenrecord that would "
        "not start). The step itself ran normally."
    ),
    PULL_FAILED: (
        "The step WAS recorded on the device, and the file could not be copied "
        "back to this machine. Nothing about the step's verdict depends on it."
    ),
    WITHHELD: (
        "No picture of this step is stored, deliberately: a credential was "
        "entered while it ran, and a frame cannot be masked the way text can."
    ),
    NOT_ATTEMPTED: (
        "No picture was attempted for this step. Nothing failed on the device."
    ),
}

#: The states, in the order a reader meets them. A consumer enumerates THIS.
STATES: tuple = tuple(NOTES)

CLIP = "clip"
FRAME = "frame"

#: Where a recording lives while it is being made. The device path charset is
#: re-checked by ``adb`` itself; this is the directory every Android build has.
REMOTE_DIR = "/sdcard"

#: The charset a stored file name may use. ``report._MEDIA_NAME`` is the
#: CONSUMER side of this one rule, and the two are pinned against each other in
#: ``tests/mobile/test_mobile_report_media.py``.
_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def media_dir(run_id: object) -> Path:
    """``runs/<run_id>/report/media``. Pure path arithmetic; creates nothing."""
    return paths.run_dir(str(run_id)) / REPORT_DIRNAME / MEDIA_DIRNAME


def _safe(value: object, limit: int = 40) -> str:
    return _UNSAFE.sub("-", str(value or ""))[:limit]


def record(kind: str, state: str, **extra: object) -> dict:
    """One media record, in the shape the case checkpoint and report share."""
    body = {"kind": str(kind), "state": str(state), "file": ""}
    body.update({str(k): v for k, v in extra.items()})
    return body


def _as_dict(action: object) -> dict:
    """One action as a plain mapping, whatever shape the caller had it in."""
    if isinstance(action, dict):
        return action
    dump = getattr(action, "model_dump", None)
    if callable(dump):
        got = dump()
        return got if isinstance(got, dict) else {}
    return dict(getattr(action, "__dict__", {}) or {})


def withholds(script: object) -> bool:
    """True when this script carries a credential, on the SAME two markers the
    trace already masks by: the model's own ``secret`` claim and the server's
    own reading of the field name. Asking the question a third way would be a
    third answer.

    Two shapes matter here and both are load-bearing.

    ``actions.parse_script`` returns a validated ``Script`` MODEL in
    ``content``, not a list, and that is exactly what ``case_runner`` hands to
    ``start_clip``. So the actions are read off ``script.actions`` and each one
    is dumped to a mapping before it is asked anything. Iterating the model
    itself raises, the never-raise fallback below then returns ``True``, and the
    result is the WORST possible failure of this feature: every step in every
    run reports ``credential_withheld``, no clip and no frame is ever stored,
    and nothing anywhere goes red.

    The gate is ``actions.VALUE_BEARING_OPS`` -- ``{"type", "ask_tester"}`` --
    not ``"type"`` alone. ``ask_tester`` IS this lane's own credential path: it
    is how a password reaches the device. Gating on ``type`` would photograph
    precisely the screen that must never be photographed, and a PNG cannot be
    masked after the fact.
    """
    try:
        items = getattr(script, "actions", script)
        for action in list(items or []):
            body = _as_dict(action)
            if str(body.get("op") or "") not in actions_mod.VALUE_BEARING_OPS:
                continue
            if body.get("secret") or actions_mod.is_credential_action(body):
                return True
    except Exception:  # never-raise: a picture is not a verdict
        logger.exception("mobile.media.withholds failed")
        return True
    return False


def records_of(run_id: str, tc_id: str) -> list:
    """Every media record already stored for a case. Never raises."""
    try:
        body = (run_store.read_case(run_id, tc_id) or {}).get("content")
        body = body if isinstance(body, dict) else {}
        return [
            item for item in list(body.get("media") or []) if isinstance(item, dict)
        ]
    except Exception:
        logger.exception("mobile.media.records_of failed")
        return []


def remember(run_id: str, tc_id: str, records: list) -> dict:
    """Append records to the case the checkpoint already owns.

    The case record is the authority on what a case did, so the media index is
    not a second file: a second index would be a second producer of one fact.
    That makes ``case_runner._checkpoint`` a CONSUMER of this key -- it rebuilds
    the case body whole on every submit, so it carries ``media`` forward
    explicitly, and ``tests/mobile/test_mobile_media_checkpoint.py`` drives
    ``submit_case`` end to end to keep that true.
    """
    try:
        fresh = (run_store.read_case(run_id, tc_id) or {}).get("content")
        if not isinstance(fresh, dict):
            # SAY SO. This is the one path where records are produced and then
            # dropped, and `finish_step`'s caller ignores the result by design
            # -- so a bare return here is a silent loss: the files are on disk
            # and nothing indexes them, and the report renders as though no
            # recording was ever attempted. It happens when no case record
            # exists yet, which in production means `start_case` did not run or
            # its write failed. The pictures are still on disk beside the run.
            logger.warning(
                "mobile.media.remember: no case record for %s/%s; %d media "
                "record(s) not indexed",
                run_id,
                tc_id,
                len([item for item in list(records or []) if isinstance(item, dict)]),
            )
            return {"error": None, "content": []}
        held = [
            item for item in list(fresh.get("media") or []) if isinstance(item, dict)
        ]
        held.extend(item for item in list(records or []) if isinstance(item, dict))
        fresh["media"] = held
        run_store.write_case(run_id, tc_id, fresh)
        return {"error": None, "content": held}
    except Exception as exc:
        logger.warning("mobile.media.remember failed: %s", exc)
        return {"error": None, "content": []}


async def start_clip(run_id: str, tc_id: str, script: object, serial: str) -> dict:
    """Begin recording this step. ``content`` is the handle finish_step wants.

    *script* is whatever ``actions.parse_script`` returned in ``content`` -- a
    ``Script`` model. A handle is ALWAYS returned, with the state that explains
    it, so the caller has exactly one thing to pass on and never a branch of its
    own.
    """
    handle = {"proc": None, "remote": "", "state": NOT_ATTEMPTED, "seq": 0}
    try:
        seq = len([r for r in records_of(run_id, tc_id) if r.get("kind") == CLIP])
        handle["seq"] = seq
        if withholds(script):
            handle["state"] = WITHHELD
            return {"error": None, "content": handle}
        if seq >= MAX_CLIPS_PER_CASE:
            handle["state"] = REFUSED
            return {"error": None, "content": handle}
        remote = (
            REMOTE_DIR
            + "/qa-agents-"
            + _safe(run_id)
            + "-"
            + _safe(tc_id)
            + "-"
            + str(seq)
            + ".mp4"
        )
        started = await adb.screenrecord_spawn(serial, remote, MAX_CLIP_SECONDS)
        if started.get("error") or not (started.get("content") or {}).get("proc"):
            handle["state"] = REFUSED
            return {"error": None, "content": handle}
        handle["proc"] = started["content"]["proc"]
        handle["remote"] = remote
        handle["state"] = OK
        return {"error": None, "content": handle}
    except Exception as exc:
        logger.warning("mobile.media.start_clip failed: %s", exc)
        handle["state"] = REFUSED
        return {"error": None, "content": handle}


async def _stop_recorder(serial: str, handle: dict) -> bool:
    """Interrupt the recorder so it CLOSES its container, then reap it.

    A killed screenrecord leaves an unplayable file, which is why this sends an
    interrupt on the device FIRST and gives the recorder a window to close its
    container, rather than killing the adb client outright.

    But the window is :data:`MAX_STOP_WAIT_S`, not ``adb.DEFAULT_TIMEOUT_S``, and
    the client IS killed once it expires. A device with no ``pkill``, or one
    where the interrupt never reaches the recorder, would otherwise block the
    step for the full adb default -- thirty seconds of a tester's wall clock,
    every step, for a picture that is already lost.

    ``pkill -INT screenrecord`` is device-GLOBAL (see the module docstring):
    there is no pid to target, and the per-device lock is what keeps that from
    reaching another run.
    """
    proc = handle.get("proc")
    if proc is None:
        return False
    await adb.shell(serial, ["pkill", "-INT", "screenrecord"], MAX_STOP_WAIT_S)
    try:
        await _wait_for(proc.communicate(), timeout=MAX_STOP_WAIT_S)
        return True
    except Exception:
        try:
            proc.kill()
            await _wait_for(proc.communicate(), timeout=MAX_STOP_WAIT_S)
        except Exception:
            pass
        return False


async def abandon(serial: str, handle: object) -> dict:
    """Stop a recorder and DELETE its file, storing no record at all.

    The one path where a clip is thrown away rather than named. When
    ``executor.replay`` returns an ERROR, ``case_runner`` returns it without
    checkpointing: there is no step result to attach a picture to and no
    terminal screen to key a frame by, so there is nowhere a state could be
    shown. What there still IS, though, is a live device process -- and a
    recorder nobody stops outlives the step, at which point the NEXT step's
    device-global ``pkill -INT screenrecord`` closes the WRONG recording.

    Bounded by :data:`MAX_STOP_WAIT_S` like every other stop, and never raises: an
    error path must not acquire a second way to fail.
    """
    held = handle if isinstance(handle, dict) else {}
    try:
        if held.get("proc") is not None:
            await _stop_recorder(serial, held)
        remote = str(held.get("remote") or "")
        if remote:
            await adb.shell(serial, ["rm", "-f", remote], MAX_STOP_WAIT_S)
    except Exception as exc:
        logger.warning("mobile.media.abandon failed: %s", exc)
    return {"error": None, "content": None}


async def finish_step(
    run_id: str, tc_id: str, serial: str, handle: object, screen_id: str = ""
) -> dict:
    """Stop the clip, capture one frame, and store both records on the case.

    ``content`` is the list of records written. Never raises, and no branch here
    can change a verdict: the caller ignores the result by design.
    """
    held = handle if isinstance(handle, dict) else {}
    state = str(held.get("state") or NOT_ATTEMPTED)
    records = []
    try:
        directory = media_dir(run_id)
        directory.mkdir(parents=True, exist_ok=True)
        seq = int(held.get("seq") or 0)
        clipped = record(
            CLIP, state, tc_id=str(tc_id), seq=seq, screen_id=str(screen_id or "")
        )
        if state == OK:
            closed = await _stop_recorder(serial, held)
            name = "clip-" + _safe(tc_id) + "-" + str(seq) + ".mp4"
            target = directory / name
            pulled = await adb.pull(serial, str(held.get("remote") or ""), str(target))
            await adb.shell(
                serial, ["rm", "-f", str(held.get("remote") or "")], MAX_STOP_WAIT_S
            )
            size = target.stat().st_size if target.is_file() else 0
            # A PULL THAT RAN AND FAILED IS NOT AN `error`. ``adb.raw`` sets
            # ``error`` only when adb could not be RUN at all (spawn failure,
            # timeout); a missing remote file comes back error=None with a
            # NON-ZERO rc and an empty stdout -- the same distinction
            # ``adb.devices`` had to learn. Reading ``error`` alone would leave
            # the size check as the only thing that noticed, which is one fact
            # answered in two places.
            body = pulled.get("content") or {}
            rc = int(body.get("rc") or 0) if isinstance(body, dict) else 0
            if pulled.get("error") or rc or not size:
                clipped["state"] = PULL_FAILED
            elif size > MAX_CLIP_BYTES:
                target.unlink(missing_ok=True)
                clipped["state"] = PULL_FAILED
            else:
                clipped["state"] = OK if closed else TRUNCATED
                clipped["file"] = name
                clipped["bytes"] = int(size)
        elif held.get("proc") is not None:
            await _stop_recorder(serial, held)
            await adb.shell(
                serial, ["rm", "-f", str(held.get("remote") or "")], MAX_STOP_WAIT_S
            )
        records.append(clipped)

        framed = record(
            FRAME,
            state if state == WITHHELD else NOT_ATTEMPTED,
            tc_id=str(tc_id),
            seq=seq,
            screen_id=str(screen_id or ""),
        )
        if state != WITHHELD and screen_id:
            shot = await adb.screencap(serial)
            data = shot.get("content") if not shot.get("error") else None
            if isinstance(data, (bytes, bytearray)) and data:
                name = "frame-" + _safe(screen_id) + ".png"
                (directory / name).write_bytes(bytes(data))
                framed["state"] = OK
                framed["file"] = name
            else:
                framed["state"] = REFUSED
        records.append(framed)
    except Exception as exc:
        logger.warning("mobile.media.finish_step failed: %s", exc)
    remember(run_id, tc_id, records)
    return {"error": None, "content": records}
