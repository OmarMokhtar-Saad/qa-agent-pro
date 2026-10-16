"""Memos of the mobile step handlers: once-per-chat static block and same-screen elision."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

from handlers.mobile.step_state import _MOBILE_SAME_SCREEN_NOTE
from tools.mobile import render as mobile_render
from tools.mobile import run_store as mobile_run_store
from tools.mobile import screenshot as mobile_screenshot

logger = logging.getLogger("tools.mcp_handlers")

_POSITION_KEYS = ("kind", "tc_id", "turn", "escapes_used", "trace", "stopped_because")


@dataclass(frozen=True)
class MemoCtx:
    """Who a memo is for (run, chat token) and what the capture said (note, unusable)."""

    run_id: str
    token: str
    note: str
    unusable: str


def _without_briefed(shown: dict, block_hash: str = "") -> dict:
    """``shown`` without the static block and the goal (the chat has seen both)."""

    return mobile_render.without_goal(mobile_render.without_static(shown, block_hash))


def _same_hash(seen: dict, block_hash: str) -> bool:
    """Whether the block now going out is the one the chat was briefed with.

    A packet with no static block (no hash) never changes the answer; a block
    whose id differs from the recorded one is re-sent.
    """
    return not block_hash or str(seen.get("static_hash") or "") == block_hash


# NEVER-RAISE, and the fail-open direction is REPEAT. This function's promise
# is that a packet always comes back; the elision is an optimisation and an
# optimisation must never be the reason a tester's model gets an error string
# where a screen belongs. `shown` is left whole, so the block goes out again --
# tokens, not a lost turn.
#
# The goal rides the same memo (fix round 3, item 1): a chat that has been
# briefed has also seen the goal, and a re-armed briefing brings both back
# together. The once-per-chat tool brief rides the same memo. Every other path
# (either id missing, memo failure) keeps `brief_needed`, so the fail-open
# direction is REPEAT.
def _mobile_briefing_memo(shown: dict, run_id: str, token: str) -> tuple[dict, bool]:
    """``(shown, brief_needed)`` after the once-per-chat static-block memo."""

    brief_needed = True
    try:
        seen = mobile_run_store.briefed_session(run_id) or {}
        block_hash = mobile_render.static_hash(shown)
        if str(seen.get("content") or "") == token and _same_hash(seen, block_hash):
            shown = _without_briefed(shown, block_hash)
            brief_needed = False
        else:
            mobile_run_store.mark_briefed(run_id, token, block_hash)
    except Exception:
        logger.exception("mcp mobile static-block memo failed")
    return shown, brief_needed


def _screen_ids(shown: dict) -> tuple[str, str, str]:
    """``(block, fingerprint, position)`` of the screen block and where it sits."""
    block = str(shown.get("screen_block") or "")
    fingerprint = hashlib.sha256(block.encode("utf-8")).hexdigest()[:12]
    position = hashlib.sha256(
        json.dumps([shown.get(key) for key in _POSITION_KEYS], default=str).encode(
            "utf-8"
        )
    ).hexdigest()[:12]
    return block, fingerprint, position


def _same_screen(last: dict, ids: tuple, token: str) -> bool:
    """Whether *last* is this chat's record of the same screen at a new position."""
    block, fingerprint, position = ids
    return bool(
        block
        and last.get("session") == token
        and last.get("fingerprint") == fingerprint
        and last.get("position") != position
    )


def _mark_screen(store: Any, memo: MemoCtx, ids: tuple) -> None:
    seen = store.ScreenMemo(memo.token, ids[1], ids[2])
    store.mark_screen(memo.run_id, seen)


# S8: the SAME screen, to the SAME chat, at a NEW position, goes as
# one line. Byte-identical text is the test, so the elision is
# lossless; a re-issued packet (same position) always goes whole,
# as does an unusable screen or one whose picture is attached.
# Fail-open is REPEAT, like the briefing memo above.
def _mobile_screen_memo(shown: dict, memo: MemoCtx) -> None:
    """The same-screen memo: one line instead of a screen the chat already has."""

    try:
        ids = _screen_ids(shown)
        block, fingerprint, position = ids
        last = (mobile_run_store.last_screen(memo.run_id) or {}).get("content") or {}
        if (
            _same_screen(last, ids, memo.token)
            and not memo.unusable
            and memo.note != mobile_screenshot.ATTACHED_NOTE
        ):
            shown["screen_block"] = _MOBILE_SAME_SCREEN_NOTE.format(fp=fingerprint)
        if block:
            _mark_screen(mobile_run_store, memo, ids)
    except Exception:
        logger.exception("mcp mobile screen memo failed")


# THE STATIC BLOCK, ONCE PER CHAT PER RUN. `render.STATIC_FIELDS` is
# three fields that are byte-identical on every packet of every lane;
# `response_schema` is NOT among them and never will be. The contract is
# docs/RETIRED_CAPABILITIES.md section 5.
#
# BOTH ids are required. A packet we cannot attribute to a run AND a
# chat is one whose model we cannot know has been briefed, and the safe
# answer there is to repeat: a wasted block costs tokens, a missing one
# costs the turn.
#
# The observation key is this module's routing value, not packet
# content: `render.packet_block` promises it adds nothing to what the
# builder made, and a 25-character hex id in the model's JSON is packet
# budget spent on a string the model has no use for. Popped from the
# COPY only -- the caller's packet still carries it for the writer.
def _mobile_shown_packet(packet: dict, memo: MemoCtx) -> tuple[dict, bool]:
    """``(copy_of_packet_to_show, brief_needed)`` with the memos applied."""
    brief_needed = True
    # A COPY: the packet is the caller's own dict, and a note written into
    # it would outlive this reply.
    shown = dict(packet)
    shown["screen_image"] = memo.note
    if memo.run_id and memo.token:
        shown, brief_needed = _mobile_briefing_memo(shown, memo.run_id, memo.token)
        _mobile_screen_memo(shown, memo)
    shown.pop("observation_id", None)
    shown.pop("screen_unusable", None)
    return shown, brief_needed
