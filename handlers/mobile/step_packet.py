"""Packet text of the mobile step handlers: heading, capture note, memo and tool brief."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from handlers.mobile.step_capture import (
    ShotTurn,
    _mobile_report_shot,
    _screen_unusable,
)
from handlers.mobile.step_memo import MemoCtx, _mobile_shown_packet
from tools.mobile import flow_replay_note
from tools.mobile import render as mobile_render
from tools.tool_briefs import MOBILE_BRIEF, MOBILE_REMINDER


@dataclass(frozen=True)
class PacketAsk:
    """One packet to render: the packet, the chat token, the run body, the budget."""

    packet: object
    session_token: str
    resolved: object = None
    budget: Any = None


def _reset_note(body: dict, first_packet: bool) -> str:
    """The "app data already cleared" line, on a first packet only."""
    resolved = body.get("resolved")
    resolved_body: dict = cast("dict", resolved) if isinstance(resolved, dict) else {}
    if first_packet and (resolved_body.get("reset_app") or {}).get("cleared"):
        return (
            "**App data was already cleared at run start** -- do not add a "
            "`clear_app_data` action; the app is already in its post-reset "
            "state.\n\n"
        )
    return ""


def _mobile_packet_header(run_id: str, body: dict, first_packet: bool) -> str:
    """The heading above a step packet, with the reset note on a first packet."""
    return (
        _reset_note(body, first_packet)
        + "## Mobile run `"
        + run_id
        + "`"
        + (" — " + str(body.get("tc_id")) if body.get("tc_id") else "")
        + "\n\nPlan the actions for the screen below and send them straight back "
        "with `qa_submit_mobile_step`.\n\n"
    )


def _packet_shown(packet: object, memo: MemoCtx) -> tuple[Any, bool]:
    """``(shown, brief_needed)``: the packet after the memos, or as it came."""
    if not isinstance(packet, dict):
        return packet, True
    return _mobile_shown_packet(packet, memo)


def _packet_block(shown: Any, token: str, brief_needed: bool) -> str:
    """The rendered packet: the tool brief while the chat needs it, else the reminder."""

    text = mobile_render.packet_block(shown, session_token=token)
    if brief_needed:
        return text + "\n\n### Tool brief (sent once per chat)\n" + MOBILE_BRIEF.strip()
    return text + "\n\n" + MOBILE_REMINDER.strip()


# THE ONE PLACE either mobile tool turns a packet into text. Both call sites
# go through here so the picture, the note that says it is attached and the
# note that says it is not cannot disagree between the two tools -- and cannot
# drift, which is what a second copy of this logic at the other call site
# would do.
#
# ``resolved`` is the run body ``session.next_packet`` / ``session.submit``
# already carry on ``content["resolved"]``; ``serial`` is read from it rather
# than re-resolved, so this costs no extra disk read.
#
# Never raises and never loses the packet: a capture that fails, times out,
# or blows up leaves the note saying so and the packet otherwise untouched.
async def _mobile_packet_text(ask: PacketAsk) -> str:
    """The rendered packet, PLUS this turn's screen captured and recorded."""
    packet, session_token = ask.packet, ask.session_token
    body = ask.resolved if isinstance(ask.resolved, dict) else {}
    note = await _mobile_report_shot(ShotTurn(packet, body, ask.budget))
    run_id = str(body.get("run_id") or "")
    memo = MemoCtx(run_id, str(session_token or ""), _screen_unusable(packet), note)
    shown, brief_needed = _packet_shown(packet, memo)
    note, shown = pop_replay_note(shown)
    return note + _packet_block(shown, session_token, brief_needed)


def pop_replay_note(shown: Any) -> tuple[str, Any]:
    """``(note + blank line, packet without it)``: an auto-replay note leads the reply (E-04)."""
    note = shown.get("auto_replay") if isinstance(shown, dict) else None
    if not note:
        return "", shown
    return str(note) + "\n\n", {k: v for k, v in shown.items() if k != "auto_replay"}


def lead_with_note(head: str, tail: str) -> str:
    """``head + tail``, but a note opening *tail* moves above *head* (E-04)."""
    if not tail.startswith(flow_replay_note.OPEN):
        return head + tail
    note, _, rest = tail.partition("\n\n")
    return note + "\n\n" + head + rest


STILL_CHANGING_NOTE = "screen: still changing after 3 s; use wait-until-text"


def still_changing_note(body: dict) -> str:
    """The line telling the model the screen never settled, else ``""``.

    Read from the last trace entry of the run body's case (the wait that
    stopped at its cap stamped ``still_changing`` there).
    """
    case = body.get("case") if isinstance(body, dict) else None
    trace = case.get("trace") if isinstance(case, dict) else None
    last = trace[-1] if isinstance(trace, list) and trace else None
    if isinstance(last, dict) and last.get("still_changing"):
        return "\n" + STILL_CHANGING_NOTE
    return ""


NO_ASSERT_NOTE = (
    "no assert was replayed (none in the script, or replay stopped before it): "
    "no verdict this turn; end scripts with an assert"
)


def no_assert_note(body: dict) -> str:
    """The line telling the model no assert was replayed this turn, else ``""``."""
    case = body.get("case") if isinstance(body, dict) else None
    trace = case.get("trace") if isinstance(case, dict) else None
    if not isinstance(trace, list) or not trace:
        return ""
    ops = {e.get("op") for e in trace if isinstance(e, dict)}
    return "" if "assert" in ops else "\n" + NO_ASSERT_NOTE
