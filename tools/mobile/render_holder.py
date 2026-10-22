"""The "another run is using the device" refusal block.

Nineteenth layer of the ``render_*`` split of ``render.py``.

``device_busy_block`` -- another run holds the device. Say WHO, and say how to
get it.

A refusal that does not name a way forward is a dead end, and this one has two:
take that run over (which is what makes the holder let go), or wait for it to
finish. It deliberately does NOT offer to break the lock -- a lock broken under
a live holder is two chats driving one device, which is the defect this whole
mechanism exists to prevent.

THE TAKE-OVER IS OFFERED ONLY WHERE IT CAN BE ACCEPTED. A run that has reached
its report cannot be continued, so telling the tester to take it over is a
closed loop: the take-over is refused for being finished, the device stays held,
and this reply says the same thing again. A finished holder is told what is true
instead -- nothing is driving it, its own chat hands the device back on its next
heartbeat, and a fresh run is one argument away.

ONE lock covers the whole lane rather than one per serial, and the reason is
stated here rather than hidden: the device stage is what picks, boots and
provisions the device, so there is no serial to key a lock on until after the
most contended step has already run. A tester with two devices is serialised
across both; that is a known cost, not a bug.
"""

from __future__ import annotations

import time

from config.settings_guard import never_raise
from tools.mobile import locks, prerun_inflight, render_busy, run_store, session_resolve
from tools.mobile.session_core import STATE_REPORT

_NO_LOCK_TEXT = (
    "## The mobile lane cannot guarantee one run at a time here\n\n"
    "This platform offers neither `fcntl` nor `msvcrt`, so nothing can "
    "stop a second chat driving the same device — and two runs on one "
    "device produce two reports that each describe a run that did not "
    "happen as recorded. Refusing is the safe answer; nothing was "
    "started."
)
_ONE_LOCK_NOTE = (
    "\nOne lock covers the whole lane, not one per device: the device is "
    "chosen and booted before any serial exists, so there is nothing to key "
    "a per-device lock on at the moment it matters most."
)
_PROVISIONING_HEAD = "A run is being set up on this device right now"
_PROVISIONING_RUNNING = (
    " in this same server, started {age} s ago — the device is being picked, "
    "booted or the app installed, and that setup call is still running. "
    "`qa_mobile_status` shows it; call `qa_mobile_test` again once it ends. "
    "Restarting the editor's MCP server ends it."
)
_PROVISIONING_STALE = (
    "A run was being set up on this device in this same server, but that "
    "setup call has ended: the hold is stale and is given back on the next "
    "`qa_mobile_test`, so call it again now."
)
_PROVISIONING_TOOL = (
    " in this same server by another tool call (a capture setup or teardown); "
    "it gives the device back when that call ends, so call `qa_mobile_test` "
    "again then."
)
_PROVISIONING_OTHER = (
    " by another chat (server pid {pid} on this machine). It ends when that "
    "setup call ends or that server exits; quitting that editor frees the "
    "device at once."
)
_UNKNOWN_HOLDER = (
    "Another process on this machine holds it. `qa_mobile_status` lists "
    "the runs this install knows about; taking one over with "
    "`qa_mobile_test run_id=...` is what releases the device."
)


# `holder` IS AN OWNER LABEL, NEVER PROSE. A caller once passed a refusal
# REASON here ("held by mrun-...") and this block dutifully told the tester
# to call `qa_mobile_test` with `run_id="held by mrun-..."` -- an
# instruction that cannot work. Rather than trusting every present and
# future call site to pass the right thing, the takeover branch is entered
# only for a label that IS a run id; anything else falls to the generic
# line, which asks for nothing the tester cannot do.
# THE GRAMMAR, not `valid_run_id`. That one asks whether a string is safe as
# a path segment and says yes to every single-token status string in this
# lane -- `handoff_failed`, `already_held`, `not_held` -- so it would have
# let one of them through as something a tester was told to pass back.
def _holder_label(body: dict) -> str:
    """The refusal's ``holder`` if it is a run id or a provisioning label, else ``""``."""
    who = str(body.get("holder") or "").strip()
    if (
        who
        and not locks.is_provisioning_owner(who)
        and not run_store.looks_like_a_run_id(who)
    ):
        return ""
    return who


def holder_finished(who: str) -> bool:
    """Whether the holder run has reached its report. Never raises.

    THE HOLDER'S OWN STATE, asked once, of the producer that answers the
    tester's `qa_mobile_status`. Only meaningful for a label that IS a run id.
    """
    if not who or locks.is_provisioning_owner(who):
        return False
    with never_raise():
        resolved = session_resolve.resolve(who)
        if resolved.get("error"):
            return False
        state = str((resolved.get("content") or {}).get("state") or "")
        return state == STATE_REPORT
    return False


def _hold_age(who: str) -> int:
    """Seconds since THIS process took a lock under *who*; ``0`` when unknown."""
    with locks._MUTEX:
        since = [h.since for h in locks._HELD.values() if h.owner == who]
    return max(0, int(time.time() - min(since))) if since else 0


def _holder_pid(who: str) -> str:
    """The pid inside a ``provisioning:<pid>:<hex>`` label, else ``"?"``."""
    parts = who.split(":")
    return parts[1] if len(parts) >= 2 and parts[1].isdigit() else "?"


# THREE TRUTHS, not one promise. This used to say the step
# "finishes within the call that started it, so call again in a moment";
# a cancelled call left the hold for the life of the server, and every
# retry read that line. Now the same-server hold says whether its call is
# still running (and how old it is), a stale one says it is healed on the
# next call, and another process's hold says which pid and what ends it.
def _provisioning_text(who: str, same: bool) -> str:
    if not same:
        return _PROVISIONING_HEAD + _PROVISIONING_OTHER.format(pid=_holder_pid(who))
    if prerun_inflight.is_inflight(who):
        return _PROVISIONING_HEAD + _PROVISIONING_RUNNING.format(age=_hold_age(who))
    if prerun_inflight.has_ended(who):
        return _PROVISIONING_STALE
    return _PROVISIONING_HEAD + _PROVISIONING_TOOL


def _finished_text(who: str) -> str:
    return (
        "Run `" + who + "` still holds it, but that run has FINISHED — "
        "nothing is driving it. It cannot be taken over, because there is "
        "nothing left to continue; the chat that ran it hands the device "
        "back on its next heartbeat, within about half a minute.\n\n"
        "1. **Call `qa_mobile_test` again shortly**, with `new_run=true` to "
        "start a fresh run on that device.\n"
        "2. **See what it did** — `qa_mobile_status` with "
        '`run_id="' + who + '"`, and `report_now=true` for its HTML report.'
    )


def _held_text(who: str) -> str:
    return (
        "Run `" + who + "` holds it. Two options, and nothing here will "
        "break its hold:\n\n"
        "1. **Take that run over** — call `qa_mobile_test` with "
        '`run_id="' + who + '"` and no `session_token`. The chat that '
        "holds it lets the device go within about half a minute of losing "
        "the run, so a retry straight after may still be refused once.\n"
        "2. **Wait for it to finish** — `qa_mobile_status` with that run id "
        "shows where it is."
    )


def _holder_paragraph(who: str, same: bool, finished: bool) -> str:
    """The paragraph that says who holds the device and what the tester can do."""
    if locks.is_provisioning_owner(who):
        return _provisioning_text(who, same)
    if who and finished:
        return _finished_text(who)
    if who:
        return _held_text(who)
    return _UNKNOWN_HOLDER


def device_busy_block(refusal: object) -> str:
    """Another run holds the device. Say WHO, and say how to get it."""
    body = refusal if isinstance(refusal, dict) else {}
    who = _holder_label(body)
    same = bool(body.get("same_process"))
    if str(body.get("reason") or "") == "no_lock_facility":
        return _NO_LOCK_TEXT
    lines = [
        "## Another run is using the device\n",
        _holder_paragraph(who, same, holder_finished(who)),
        render_busy.facts_block(who),
        _ONE_LOCK_NOTE,
    ]
    return "\n".join(lines)
