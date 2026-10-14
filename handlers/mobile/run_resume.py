"""Part of the mobile_run split (see handlers/mobile/run.py): resume."""

from __future__ import annotations

import tools.mobile.device_lock as device_lock
import tools.mobile.session_core as session_core
import tools.mobile.session_finish as session_finish
import tools.mobile.session_runs as session_runs
from handlers import _common
from handlers.mobile import step as mobile_step
from tools.mobile import render as mobile_render
from tools.mobile import run_store
from tools.untrusted import single_line as _safe


# Notes for ``_mobile_resume_device_lock`` (moved out of the body to keep it short):
# Function-local imports, like handle_mobile_test: tools.mobile is absent
# from the distribution build.
#
# THE DEVICE LOCK, RE-ACQUIRED ON EVERY RESUME. The MCP process
# restarts on every code and `.env` edit and a run survives that on
# disk, so a resumed run has to take the lock again -- the kernel
# dropped the old one when the old process died. Idempotent for a
# run this process already holds: no syscall, no write.
#
# THE HEARTBEAT STARTS BEFORE THE LOCK, and the order is the
# fix, not a detail. It used to start after
# `_mobile_resume_device_stage`, which returns POINTER replies ("the
# emulator is still starting", a re-run preflight) -- so a resume
# that hit one of those held the device with `heartbeat._WRITERS`
# still empty, and nothing in this process could ever give it back.
# Abandon that chat and the emulator is dead to every other process
# until the server restarts, which is precisely the hazard this
# design refuses to accept for the pre-run phase.
#
# Starting it first is safe: this chat already holds the run's LEASE
# (checked by the caller), which is the only thing the writer refreshes.
#
# The device this run is on comes from its own manifest, inside
# `take_device_lock`: a resume must hold the device it recorded, not
# whatever is attached now.
#
# A REBOOTED EMULATOR COMES BACK ON A DIFFERENT SERIAL, and the
# stage above writes the fresh one to the manifest. The lock taken a
# moment ago names the OLD device, so re-take: the acquire is a
# no-op when nothing moved, and when something did it takes the real
# device and drops the stale hold rather than driving a device this
# run does not own.
async def _mobile_resume_device_lock(run_id: str, token: str, budget: object) -> str:

    mobile_step.mobile_start_heartbeat(run_id, token)
    taken = (device_lock.take_device_lock(run_id, lease=token) or {}).get(
        "content"
    ) or {}
    if not taken.get("acquired"):
        return mobile_render.device_busy_block(taken)
    device = await mobile_step.mobile_resume_device_stage(run_id, budget=budget)
    if device:
        return device
    retaken = (device_lock.take_device_lock(run_id, lease=token) or {}).get(
        "content"
    ) or {}
    if not retaken.get("acquired"):
        return mobile_render.device_busy_block(retaken)
    return ""


# Notes for ``_mobile_release_lost_run`` (moved out of the body to keep it short):
# Displaced. Give the device back NOW rather than waiting
# for this process's heartbeat writer to notice on its next
# beat: this is the holder releasing its own lock,
# synchronously, in the call that learned it lost the run.
#
# PRESENT THIS CHAT'S OWN LEASE. This branch has just been told
# it lost the run, so it must NOT claim holder authority: if the
# lock is held under this chat's token the release is right, and
# if it is held under the NEW holder's token it must be refused.
# Passing nothing used to mean `as_holder=True`, which released
# the lock the current holder was driving under.
async def _mobile_release_lost_run(run_id: str, claimed: dict) -> str:

    held = claimed.get("content") or {}
    await session_finish.finish_device(
        run_id,
        lease=str((claimed.get("content") or {}).get("session_token") or ""),
    )
    return mobile_render.takeover_block(
        run_store.takeover_message(run_id, str(held.get("holder") or "?"))
    )


# Notes for ``_mobile_resume`` (moved out of the body to keep it short):
# An existing run: lease first, then the device, then the next packet.
#
# A resume can arrive days later, in a new chat, on a machine that
# has rebooted since: check the DEVICE before producing a packet for
# it. Deliberately AFTER the lease check, so a displaced chat can
# never boot an emulator it does not own or take its lock.
#
# `past_gate` rides along: without it the gate is re-decided inside
# _mobile_next, which does not know the tester already answered, and
# the run never advances past case 20.
async def _mobile_resume(
    call: tuple[str, str, bool], budget: object, progress: _common.ProgressCb
) -> str:
    run_id, session_token, continue_run = call
    token, early = await _mobile_resume_lease(run_id, session_token, budget)
    if early is not None:
        return early
    early = _mobile_resume_gate(run_id, token, continue_run)
    if early is not None:
        return early
    return await mobile_step.mobile_next(
        run_id,
        token,
        mobile_step.NextOpts(progress=progress, past_gate=continue_run, budget=budget),
    )


def _mobile_warn(error: object, limit: int) -> str:
    """The warning line for a lease/resolve error."""

    return "⚠️ " + _safe(error, limit)


async def _mobile_resume_lease(
    run_id: str, session_token: str, budget: object
) -> tuple[str, str | None]:
    """``(token, early_reply)``: the lease, then the device lock."""

    claimed = session_core.claim(run_id, session_token, force=not session_token)
    if claimed.get("error"):
        return "", _mobile_warn(claimed["error"], 300)
    held = claimed.get("content") or {}
    if held.get("state") != run_store.HELD:
        return "", await _mobile_release_lost_run(run_id, claimed)
    token = str(held.get("session_token") or "")
    blocked = await _mobile_resume_device_lock(run_id, token, budget)
    return token, (blocked or None)


def _mobile_resume_gate(run_id: str, token: str, continue_run: bool) -> str | None:
    """The early reply (resolve error or gate block), or ``None`` to go on."""

    resolved = session_runs.resolve(run_id, token)
    if resolved.get("error"):
        return _mobile_warn(resolved["error"], 400)
    body = resolved["content"] or {}
    if body.get("gate") and not continue_run:
        return mobile_render.gate_block(
            {
                "done": body.get("done"),
                "total": body.get("total"),
                "failed": body.get("failed"),
            }
        )
    return None
