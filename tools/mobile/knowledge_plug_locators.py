"""Executor plug locators: the learned locator queue and the similarity heal.

Locators are strings ``"rid:<v>"``, ``"label:<v>"`` or ``"text:<v>"``. A learned
element row (snapshot ``"elements"``) holds its fingerprint and a ranked queue.

``resolve_target`` acts only when the default resolution MISSED a targeted
action: it tries the queue in order (first node found replaces the target), then
a similarity heal against the stored fingerprint. A heal needs a score >=
``HEAL_MIN_SCORE`` and a unique best (margin ``HEAL_MARGIN``); an ambiguous one
never taps. Nothing here touches the DB; what was tried and used goes into
``ctx.kbuf`` and ``post_step`` copies it onto the step entry (``locator_used``,
``locator_tried``) for the learning stage. The destructive guard still judges
whatever node comes back.
"""

from __future__ import annotations

import json  # noqa: F401
import logging
from typing import Any, cast  # noqa: F401

from tools.mobile import (  # noqa: F401
    knowledge_db,
    knowledge_hooks,
    knowledge_limits,
    screen_key,
)
from tools.mobile.kplg_calls import Post, Resolve
from tools.mobile.kplg_loc_fp import (  # noqa: F401
    HEAL_MARGIN,
    ancestors_of,
    best_match,
    fp_of,
    score,
)
from tools.mobile.kplg_loc_heal import resolve
from tools.mobile.kplg_loc_str import (  # noqa: F401
    FP_PREFIX,
    HEAL_PREFIX,
    PENDING,
    fp_token,
    locator_of,
    own_locators,
    parse_queue,
    split_locator,
)

logger = logging.getLogger(__name__)

PRIORITY = 40


def snapshot(conn, facts) -> dict:
    """``{"elements": {screen_key: [row]}}`` of the active element rows."""
    out: dict = {}
    rows = knowledge_db.rows(
        conn,
        "elements",
        knowledge_db.Where(
            "status = 'active' AND invalid_at IS NULL",
            (),
            knowledge_limits.SNAPSHOT_MAX_ROWS,
        ),
    )
    for row in sorted(rows, key=lambda r: r["id"]):
        out.setdefault(row["screen_key"], []).append(row)
    return {"elements": out}


async def resolve_target(ctx, call: Resolve):
    """Queue then heal, only when the default resolution missed a targeted step."""
    try:
        return resolve(ctx, call)
    except Exception:
        logger.exception("knowledge locators resolve_target failed")
        return knowledge_hooks.NONE


def post_step(ctx, call: Post) -> None:
    """Copy the pending locator record onto the step entry (in memory only)."""
    entry = call.entry
    buf = getattr(ctx, "kbuf", None)
    if not isinstance(buf, list) or not isinstance(entry, dict):
        return
    pending = [e for e in buf if isinstance(e, dict) and e.get("kind") == PENDING]
    if not pending:
        return
    buf[:] = [e for e in buf if not (isinstance(e, dict) and e.get("kind") == PENDING)]
    last = pending[-1]
    entry["locator_used"] = str(last.get("used") or "")[:200]
    entry["locator_tried"] = list(last.get("tried") or [])[:10]
