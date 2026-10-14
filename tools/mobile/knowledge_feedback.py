"""Host feedback on applied knowledge (design sec 13).

The tester's model may report ``{item_id, verdict, observation, proposed}`` on an
item it was told about. This module validates that input, weighs it against the
dump, and records it in memory (``ctx.kbuf``); a plug ``flush`` persists it as
``feedback`` events; the gate consumes them at run end.

Nothing here calls a model, touches the DB on the hot path (``record`` and
``post_step`` are in-memory) or raises. Secrets are refused; stored text is only
ever echoed through ``wrap_untrusted``.
"""

from __future__ import annotations

import json  # noqa: F401
import logging
import re  # noqa: F401
import sqlite3  # noqa: F401
import time  # noqa: F401
from typing import Any, TypedDict  # noqa: F401

from tools.mobile import app_knowledge, knowledge_hooks  # noqa: F401
from tools.mobile import knowledge_db as kdb  # noqa: F401
from tools.mobile import knowledge_limits as limits  # noqa: F401
from tools.mobile.knowledge_feedback_check import (  # noqa: F401
    _rids,
    validate,
    validate_checked,
    verify,
)
from tools.mobile.knowledge_feedback_core import (  # noqa: F401
    _CONTEXT_WORDS,
    _ENV_PAIR,
    _IGNORED,
    EVENT,
    VERDICTS,
    Anchor,
    Feedback,
    _text,
    context_of,
    describe,
    echo,
)
from tools.mobile.knowledge_feedback_store import (  # noqa: F401
    Stamp,
    flush,
    latest_proposal,
    match_existing,
    run_feedback,
)
from tools.mobile.kplg_calls import Post
from tools.untrusted import single_line, wrap_untrusted  # noqa: F401

logger = logging.getLogger(__name__)

PRIORITY = 90


def record(ctx: Any, items: Any, anchor: Anchor = Anchor()) -> int:
    """Append validated *items* to ``ctx.kbuf`` (in memory only). Returns how many fit."""
    taken = 0
    try:
        for fb in validate(items):
            event = {
                "kind": EVENT,
                "item_id": fb["item_id"],
                "verdict": fb["verdict"],
                "observation": fb["observation"],
                "proposed": fb["proposed"],
                "weight": verify(fb, anchor.screen),
                "index": int(anchor.step),
            }
            if knowledge_hooks.kbuf_add(ctx, event):
                taken += 1
    except Exception:
        logger.exception("knowledge feedback record failed")
    return taken


def post_step(ctx: Any, call: Post) -> None:
    """Plug seam: a step entry carrying ``feedback`` is recorded in memory."""
    try:
        entry = call.entry
        if isinstance(entry, dict) and entry.get("feedback"):
            record(
                ctx,
                entry.get("feedback"),
                Anchor(call.after, int(entry.get("index", -1))),
            )
    except Exception:
        logger.exception("knowledge feedback post_step failed")
