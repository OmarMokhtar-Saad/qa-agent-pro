"""The facts and exact next calls of a busy answer (fix/1580 C02/C13).

A call that cannot start because another run holds the device used to say who
holds it and nothing else, so the host retried blindly. This block adds what a
host needs to choose: the holder run, the tool running, since when, how long it
has been idle, when the idle release will free the device, and the next calls.
Never raises; an unknown fact is left out rather than guessed.
"""

from __future__ import annotations

import time

from config.settings_guard import never_raise
from tools.mobile import (
    checkpoint_clock,
    lock_reaper,
    locks,
    prerun_inflight,
    run_store,
)


def _clock(epoch: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(epoch))


def _tool_running(who: str) -> str:
    if locks.is_provisioning_owner(who):
        running = prerun_inflight.is_inflight(who)
        return "a setup call (qa_mobile_test)" if running else "none (stale setup)"
    return "the run's own steps (qa_submit_mobile_step)"


def idle_s(who: str) -> float | None:
    if locks.is_provisioning_owner(who):
        return None
    persisted = checkpoint_clock.last_checkpoint_ts(who)
    return lock_reaper.idle_seconds(who, persisted)


def _since(who: str) -> str | None:
    """``hh:mm`` the run record says *who* started, or None when unknown."""
    with never_raise():
        made = (run_store.read_manifest(who).get("content") or {}).get("created")
        if isinstance(made, (int, float)) and not isinstance(made, bool) and made > 0:
            return time.strftime("%H:%M", time.localtime(made))
    return None


def _facts(who: str) -> list[str]:
    lines = [f"- holder run: `{who}`", f"- tool running: {_tool_running(who)}"]
    since = _since(who)
    if since:
        lines.append(f"- since: {since}")
    idle = idle_s(who)
    if idle is not None:
        left = max(0, int(lock_reaper.IDLE_RELEASE_TIMEOUT_S - idle))
        lines.append(f"- idle for: {int(idle)} s")
        lines.append(
            f"- auto-release: at {_clock(time.time() + left)} (in {left} s) "
            "if it stays idle"
        )
    return lines


def _calls(who: str) -> list[str]:
    calls = ["- wait: call this tool again shortly (it answers at once if busy)"]
    if run_store.looks_like_a_run_id(who):
        calls.append(f'- end it: `qa_mobile_stop target="{who}"` (answers at once)')
        calls.append(f'- take over: `qa_mobile_test run_id="{who}"`')
    return calls


def facts_block(who: str) -> str:
    """The busy-answer facts and next calls for holder *who*; ``""`` if unknown."""
    if not who:
        return ""
    with never_raise() as g:
        head = ["\n**Busy: what is going on, and what you can call**"]
        return "\n".join(head + _facts(who) + _calls(who))
    assert g.exc is not None
    return ""
