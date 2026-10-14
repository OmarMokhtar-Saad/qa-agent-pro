"""Executor plug shortcuts: replay a learned multi-step sequence in one go.

``pre_step`` (PRIORITY 30): an ACTIVE shortcut whose ``start_key`` equals the
live screen key, whose precondition rids are on screen and whose first steps
match the model's upcoming steps (op + target rid) is inserted as one
``insert_steps`` directive. It needs the model's upcoming steps
(``ctx.script_ahead``, current step first); without them nothing fires. A
precondition or sequence mismatch skips it, counts a fail in ``ctx.kbuf``
(``flush`` persists it; ``STALE_AFTER_FAILS`` fails make it stale) and the model
carries on silently. Never fires while ``ctx.route_expect`` is set, so a saved
route's own expectation check wins. Each shortcut fires at most once per run.
Hot path: zero DB I/O. Seams never raise.
"""

from __future__ import annotations

import json  # noqa: F401
import logging
from typing import Any

from tools.mobile import knowledge_db, knowledge_hooks, knowledge_limits  # noqa: F401
from tools.mobile.knowledge_hooks import Directive
from tools.mobile.knowledge_plug_popups import op_rid, screen_rids  # noqa: F401
from tools.mobile.kplg_calls import Post, Pre
from tools.mobile.kplg_short_db import flush, snapshot  # noqa: F401
from tools.mobile.kplg_short_fire import Window, _advance, _live, _try

logger = logging.getLogger(__name__)

PRIORITY = 30


def _rows(ctx: Any) -> list:
    knowledge = getattr(ctx, "knowledge", None)
    snap = knowledge.get("snap") if isinstance(knowledge, dict) else None
    rows = snap.get("shortcuts") if isinstance(snap, dict) else None
    return rows if isinstance(rows, list) else []


def pre_step(ctx, call: Pre) -> Directive:
    if getattr(ctx, "route_expect", None):
        return knowledge_hooks.NONE
    key = str((call.entry or {}).get("screen_key") or "")
    ahead = list(getattr(ctx, "script_ahead", None) or [])
    if not key or not ahead or not _rows(ctx):
        return knowledge_hooks.NONE
    win = Window(call.screen, ahead)
    for row in _rows(ctx):
        if row.get("start_key") == key:
            got = _try(ctx, row, win)
            if got.kind != "none":
                return got
    return knowledge_hooks.NONE


def post_step(ctx, call: Post) -> None:
    """Count down the steps of the shortcut in flight. A step that is not ``ok``
    turns its fired event into a ``steps`` fail; only a shortcut that ran to the
    end counts as a success in ``flush``. Only the step the shortcut expects next
    counts: a popup dismiss spliced in between is not one of its steps, and an
    insert the executor refused (``refused``) never runs any."""
    try:
        ev = _live(ctx)
        if ev is None or not isinstance(call.entry, dict):
            return
        _advance(ev, call.entry)
    except Exception:
        logger.exception("knowledge shortcuts post_step failed")
