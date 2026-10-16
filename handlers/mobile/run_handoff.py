"""Part of the mobile_run split (see handlers/mobile/run.py): handoff."""

from __future__ import annotations

import asyncio
import logging

import tools.mobile.device_lock as device_lock
import tools.mobile.session_core as session_core
from handlers import _common
from handlers.mobile import step as mobile_step
from handlers.mobile.run_device_prefs import _mobile_a11y_stage
from handlers.mobile.run_hooks import mobile_run
from tools.mobile import knowledge_run, step_timing
from tools.mobile import render as mobile_render

logger = logging.getLogger("tools.mcp_handlers")


# Notes for ``_mobile_hand_off`` (moved out of the body to keep it short):
#
#     ONE place, because the explore and suite branches above both reach it and
#     two copies of a lock hand-off is how the two drift -- the same argument
#     ``session.claim``'s docstring makes about the two handlers.
#
#     Three things happen here, in this order, and the order matters:
#
#     1. ``claim`` -- a fresh run, so this always succeeds and mints the token.
#     2. ``relabel_device_lock`` -- the run exists on disk now, so its id is a
#        DURABLE identity: it can be found again from any chat, and its heartbeat
#        can give the device back. The lock therefore moves from the caller's
#        per-process placeholder to the run, keeping the SAME file descriptor and
#        so the same kernel lock -- not released and retaken, which would open a
#        window for another process.
#     3. ``_mobile_start_heartbeat`` -- a brand-new run had NO heartbeat writer
#        until now, which left its device lock with no agent that could ever
#        release it. Starting it here is what makes the lock's lifetime the
#        writer's lifetime.
#
#     The relabel comes BEFORE ``_mobile_next``, which may release the lock when a
#     run lands straight on its report: a release under the placeholder label
#     would silently fail and leak the hold.
#
async def _mobile_hand_off(
    run_id: str, *, pre_run_owner: str, progress: _common.ProgressCb = None
) -> tuple:
    """Claim a freshly planned run and give it the device lock. ``(markdown, True)``."""
    token, refusal = await _mobile_claim_fresh_run(run_id)
    if refusal:
        return refusal, False
    refused = _mobile_take_device_lock(run_id, token, pre_run_owner)
    if refused:
        return refused, False
    mobile_step.mobile_start_heartbeat(run_id, token)
    notes = await _mobile_first_stage_notes(run_id)
    rendered = await mobile_step.mobile_next(
        run_id, token, mobile_step.NextOpts(progress=progress, first_packet=True)
    )
    for note in notes:
        if note:
            rendered = note + "\n\n" + rendered
    return rendered + _start_timing_line(run_id), True


def _start_timing_line(run_id: str) -> str:
    """The ``⏱`` line of the start (launch, wait, first settle); ``""`` if unarmed."""
    timing = step_timing.line()
    if timing:
        logger.info("mobile start %s: %s", run_id, timing)
    return "\n\n" + timing if timing else ""


# Notes for ``_mobile_claim_fresh_run`` (moved out of the body to keep it short):
# A claim on a freshly planned run is documented to always succeed, so
# this is a "cannot happen" -- which is exactly why it was never
# inspected, and exactly the shape that strands a device: with no token
# the heartbeat refuses to start, and a lock with no writer and no lease
# authority is held until the process exits. Give the device back and
# say so rather than proceeding on an empty authority.
async def _mobile_claim_fresh_run(run_id: str) -> tuple[str, str]:
    """Claim a freshly planned run: ``(token, "")`` or ``("", refusal_markdown)``."""

    await asyncio.to_thread(
        knowledge_run.init_manifest, run_id, mobile_step._MOBILE_RUN_ENV.get()
    )
    claimed = session_core.claim(run_id, "", force=True)
    token = str((claimed.get("content") or {}).get("session_token") or "")
    if claimed.get("error") or not token:
        return "", _unclaimed_refusal(run_id, claimed)
    return token, ""


def _unclaimed_refusal(run_id: str, claimed: dict) -> str:
    """Log the failed claim and return the refusal markdown for the tester."""
    logger.warning(
        "mcp mobile: could not claim %s after planning it (%s)",
        run_id,
        str(claimed.get("error") or "no session token")[:200],
    )
    return (
        "⚠️ Run `"
        + run_id
        + "` was planned but could not be claimed, so nothing was started "
        "and the emulator was not taken. Call `qa_mobile_test` with that "
        "`run_id` to pick it up."
    )


# Notes for ``_mobile_take_device_lock`` (moved out of the body to keep it short):
# The lock is the RUN's now, so record the lease it is held under:
# the writer started below is the only thing that may release it, and a
# later chat's writer for the same run must not be able to.
#
# THE HAND-OFF IS CHECKED. It used to return True unconditionally, so a
# failed relabel left the caller's `finally` skipped and the lock held
# under a label nothing would ever release again -- while the run drove
# the device owning nothing. Reporting False hands the device back and
# refuses the run, which is the truthful answer: this call could not
# take ownership of the device it was about to drive.
#
# The real owner LABEL, which `relabel` now returns. The
# reason string used to go here and the reply then told the
# tester to pass it as a `run_id`.
def _mobile_take_device_lock(run_id: str, token: str, pre_run_owner: str) -> str:
    """Move the device lock to the run: ``""`` on success, else a refusal."""

    moved = (device_lock.relabel_device_lock(pre_run_owner, run_id) or {}).get(
        "content"
    ) or {}
    if moved.get("relabelled"):
        device_lock.take_device_lock(run_id, lease=token)
    if not moved.get("relabelled"):
        return _lock_handoff_refusal(run_id, moved)
    return ""


def _lock_handoff_refusal(run_id: str, moved: dict) -> str:
    """Log the failed relabel and return the device-busy block."""

    logger.warning(
        "mcp mobile: could not hand the emulator lock to %s (%s)",
        run_id,
        moved.get("reason") or "no reason given",
    )
    return mobile_render.device_busy_block(
        {
            "holder": str(moved.get("holder") or ""),
            "same_process": True,
            "reason": "handoff_failed",
        }
    )


# Notes for ``_mobile_first_stage_notes`` (moved out of the body to keep it short):
# THE KEYBOARD, BEFORE THE FIRST DUMP EXISTS. This is the last point at
# which the run holds the device and no screen has been handed to the model
# yet -- `_mobile_next` below produces that screen. Doing it here rather
# than at the first `type` op closes a window rather than a bug: selecting
# an input method can CHANGE THE SCREEN (the old keyboard goes, ours may
# come up), so an ensure that runs mid-run replays a plan against a screen
# that is no longer the one it was planned on.
#
# Not fatal. Typing may be impossible while tapping and asserting are fine,
# and refusing a whole run for that would be worse than the defect -- so the
# outcome is REPORTED and the per-case hook still refuses the individual
# case that types.
#
# ANIMATIONS OFF, BEFORE THE FIRST DUMP for the same reason: the first
# screen handed to the model is the first one that must be settled. The two
# stages run TOGETHER (S13): they touch different settings, and neither
# raises, so neither can cancel the other.
#
# After the gather, not inside it: a parity dump taken mid IME switch would
# fail for the wrong reason.
async def _mobile_first_stage_notes(run_id: str) -> tuple[str, str, str]:
    """The notes of the three pre-dump device stages: ``(a11y, animations, keyboard)``."""
    keyboard_note, animations_note = await asyncio.gather(
        mobile_run.mobile_keyboard_stage(run_id),
        mobile_run.mobile_animations_stage(run_id),
    )
    a11y_note = await _mobile_a11y_stage(run_id)
    return a11y_note, animations_note, keyboard_note
