"""Claim a mobile run and take its device lock for one step."""

from __future__ import annotations

from handlers.mobile.step_hooks import mobile_step
from handlers.mobile.step_state import _mark_mobile_step_held
from tools.mobile import device_lock, run_store, session_core, session_finish
from tools.mobile import render as mobile_render
from tools.untrusted import single_line as _safe


async def _displaced_reply(run_id: str, claimed: dict, held: dict) -> tuple[str, str]:
    """The takeover reply for a run another chat now holds, device given back."""

    # Displaced: give the device back in this call rather than
    # waiting for the heartbeat writer's next beat -- under THIS chat's
    # own lease, never as the holder, because this branch has just been
    # told it is not.
    await session_finish.finish_device(
        run_id,
        lease=str((claimed.get("content") or {}).get("session_token") or ""),
    )
    return (
        mobile_render.takeover_block(
            run_store.takeover_message(run_id, str(held.get("holder") or "?"))
        ),
        "",
    )


def _lock_for_step(run_id: str, token: str) -> str | None:
    """None once this step holds the device lock, else the device-busy block."""

    # THE REPLAY IS THE DEVICE-TOUCHING STEP. `session.submit` replays the
    # tester's action script through adb, so this entry takes the lock too
    # -- it is not enough that the call which produced the packet held it.
    # In the ordinary case this process already holds it for this run and
    # the acquire is REENTRANT: no syscall, no write. After a server restart
    # it acquires for real, which is what has to happen anyway.
    taken = (device_lock.take_device_lock(run_id, lease=token) or {}).get(
        "content"
    ) or {}
    if not taken.get("acquired"):
        return mobile_render.device_busy_block(taken)
    return None


def _claim_run(run_id: str, session_token: str) -> tuple[dict, dict, bool]:
    """``(claimed, held, still_held)``: the claim reply and whether this chat holds."""

    claimed = session_core.claim(run_id, session_token)
    held = claimed.get("content") or {}
    return claimed, held, held.get("state") == run_store.HELD


async def _mobile_step_acquire(
    run_id: str, session_token: str
) -> tuple[str | None, str]:
    """Claim the run and take the device lock: ``(early_reply, token)``.

    ``early_reply`` is None when the step may go on.
    """
    claimed, held, still_held = _claim_run(run_id, session_token)
    if claimed.get("error"):
        return "⚠️ " + _safe(claimed["error"], 300), ""
    if not still_held:
        return await _displaced_reply(run_id, claimed, held)
    token = str(held.get("session_token") or "")
    mobile_step.mobile_start_heartbeat(run_id, token)
    busy = _lock_for_step(run_id, token)
    if busy is not None:
        return busy, token
    _mark_mobile_step_held()
    return None, token
