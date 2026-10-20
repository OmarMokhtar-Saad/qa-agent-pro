"""A planner's script submitted: replay it, decide a verdict, checkpoint."""

from __future__ import annotations

import logging

from tools.mobile import host_auto_replay, ime_session, media, run_store, step_timing
from tools.mobile.case_run_counts import case_view
from tools.mobile.case_run_script import (
    _fold_queue,
    _parse_submission,
    _ready_keyboard,
    _refused_script,
)
from tools.mobile.case_run_slice import _slice_evidence
from tools.mobile.case_run_types import Submission, _Case, _Intake, _Ref, _Step
from tools.mobile.case_run_verdict import _outcome_checkpoint, _read_step
from tools.mobile.replay.context import Context
from tools.mobile.replay.replay import replay
from tools.mobile.shown_refs import map_refs

# Same object as case_run_consts.logger (getLogger is a registry); a local
# binding lets ruff see the handler below as logged (BLE001).
logger = logging.getLogger("tools.mobile.case_runner")


def _note_fallback(ref: _Ref, before: str) -> None:
    """Memoise a keyboard that fell back during the replay."""
    after = str(getattr(ref.ctx, "typing_fallback", "") or "")
    if after and not before:
        # R1d: the keyboard was ready but would not come up inside the replay.
        # Memoise it so the next typing case does not retry the install.
        ime_session.write_fallback(ref.run_id, after)


# Replay the script with the screen recorder running: ``(replayed, clip)``.
#
# The pixels of this step. Started BEFORE the replay and stopped after it, so
# the clip covers exactly what the planner's script did. Never branches the
# run: a refused recorder returns a handle carrying the reason, and the
# verdict path cannot see the difference. `script` is a validated
# `actions.Script` MODEL, not a list -- `media.withholds` reads `.actions` off
# it for that reason.
async def _replay_recorded(ref: _Ref, script: object) -> tuple[dict, object]:
    """Replay the script with the screen recorder running: ``(replayed, clip)``."""
    clip_ref = media.ClipRef(ref.run_id, ref.tc_id, ref.ctx.serial, True)
    started = await media.start_clip(clip_ref, script)
    clip = started.get("content")
    before = str(getattr(ref.ctx, "typing_fallback", "") or "")
    replayed = await replay(script, ref.ctx)
    _note_fallback(ref, before)
    if replayed.get("error"):
        # THE LEAK THIS PATH WOULD OTHERWISE BE. The caller returns BEFORE
        # `finish_step`, so the recorder started above would outlive the step
        # -- and the next step's device-global `pkill -INT screenrecord` would
        # then close the WRONG recording. `abandon` stops it and deletes the
        # file without storing a record: a step with no result has nowhere to
        # show a picture or a reason.
        await media.abandon(ref.ctx.serial, clip)
    return replayed, clip


# Outside any `if`: a step that changed nothing still started a recorder, and
# a recorder nobody stops is a device process that outlives the run. This
# writes the media records onto the case, and `_checkpoint` rebuilds that body
# whole -- which is why it carries `media` forward explicitly.
#
# App evidence (plan D5): ONE slice per replay, after the trace is final and
# before the checkpoint that carries the record forward. The refused-script
# path never reaches here: nothing ran, so there is nothing to slice.
async def _close_step_evidence(step: _Step, clip: object, ctx: Context) -> dict:
    """Stop the recorder and slice the app evidence; returns the case's crash."""
    screen_id = str((step.new_screen or {}).get("screen_id") or "")
    await step_timing.timed(
        "evidence",
        media.finish_step(
            media.ClipRef(step.run_id, step.tc_id, ctx.serial, True), clip, screen_id
        ),
    )
    return await step_timing.timed(
        "evidence", _slice_evidence(step.run_id, step.tc_id, ctx)
    )


def _prior_queue(run_id: str, tc_id: str) -> list:
    """The actions a previous, refused submission left queued on this case."""
    prior_checkpoint = (run_store.read_case(run_id, tc_id) or {}).get("content")
    prior_checkpoint = prior_checkpoint if isinstance(prior_checkpoint, dict) else {}
    return list(prior_checkpoint.get("queued_actions") or [])


def _intake(run_id: str, case: object, raw_script: object) -> _Intake:
    """Fold, validate and so either refuse the submission or hand over the script."""
    view = case_view(case)
    tc_id = view.get("tc_id") or ""
    ours = _Case(run_id, tc_id, view)
    if not run_store.valid_tc_id(tc_id):
        return _Intake({"error": "Invalid case id.", "content": None}, ours, None)
    queued_in = _prior_queue(run_id, tc_id)
    raw_script = map_refs(raw_script, run_id)
    raw_script, fold_refusal = _fold_queue(raw_script, queued_in)
    if fold_refusal is not None:
        return _Intake(_refused_script(ours, fold_refusal, queued_in), ours, None)
    parsed = _parse_submission(raw_script, bool(queued_in))
    if parsed.get("error"):
        refusal = _refused_script(ours, str(parsed["error"]), queued_in)
        return _Intake(refusal, ours, None)
    return _Intake(None, ours, parsed["content"])


async def _replay_and_checkpoint(ref: _Ref, view: dict, script: object) -> dict:
    """Replay the script, close its evidence and checkpoint the outcome."""
    replayed, clip = await _replay_recorded(ref, script)
    if replayed.get("error"):
        return replayed
    step = _read_step(ref.run_id, ref.tc_id, replayed)
    crash = await _close_step_evidence(step, clip, ref.ctx)
    step, note = await host_auto_replay.after_stop(step, ref)
    if note:  # the replayed steps have their own logcat to check for a crash
        crash = await _slice_evidence(ref.run_id, ref.tc_id, ref.ctx) or crash
    outcome = _outcome_checkpoint(step, view, crash)
    host_auto_replay.annotate(outcome, note)
    if not (outcome.get("content") or {}).get("packet"):
        await media.flush(ref.run_id)
    return outcome


# ``{"error", "content": {"tc_id", "verdict", "status", "reason", "trace",
# "escapes", "packet"}}``. ``packet`` is the NEXT thing to ask for -- an
# escape-hatch job on ``needs_model``, a credential request on
# ``needs_tester`` -- and ``None`` once the case is terminal.
async def submit_case(sub: Submission) -> dict:
    """Validate a planner's script, replay it, decide a verdict, checkpoint."""
    run_id, case, ctx, raw_script, screen = sub
    try:
        await media.flush(run_id)
        intake = _intake(run_id, case, raw_script)
        if intake.early is not None:
            return intake.early
        await _ready_keyboard(intake.script, ctx, run_id)
        ctx.screen = screen if isinstance(screen, dict) else None
        ref = _Ref(run_id, intake.case.tc_id, ctx)
        return await _replay_and_checkpoint(ref, intake.case.view, intake.script)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.case_runner.submit_case failed")
        return {"error": str(exc), "content": None}
