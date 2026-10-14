"""Learning stage shortcuts: mine repeated OK step sequences, seed from routes.

A window of ``SHORTCUT_MIN_LEN..SHORTCUT_MAX_LEN`` consecutive OK ``tap``/``type``
steps (each ends on the next one's start screen, an rid on every step) is a
shortcut candidate named by a hash of its start key, end key and steps. Typed
text is NEVER stored: a ``type`` step keeps its rid and field name only and the
text becomes ``{arg_n}`` (``args_json``). A sequence that names a credential
field or trips the secret scrub is refused. A candidate that has been seen in
``SHORTCUT_MIN_RUNS`` distinct runs is gate-ready (the gate activates it); until
then, and always for a candidate, nothing replays it. Saved routes are folded in
through ``knowledge_routes_bridge`` (read only). Never raises.
"""

from __future__ import annotations

import hashlib  # noqa: F401
import json  # noqa: F401
import logging
from typing import Any  # noqa: F401

from tools.mobile import (  # noqa: F401
    app_knowledge,
    knowledge_db,
    knowledge_limits,
    knowledge_routes_bridge,
)
from tools.mobile.knowledge_learn import StageResult
from tools.mobile.kstg_mine import (  # noqa: F401
    MAX_WINDOWS,
    candidates,
    chains,
    secret_refusal,
)
from tools.mobile.kstg_short_store import Seen, by_name, insert, seen_again

logger = logging.getLogger(__name__)

NEW_PER_RUN = 10


def _mine(conn, lc) -> tuple[int, int, int]:
    """``(rows written, gate-ready, refused)``."""
    found, refused = candidates(lc.steps)
    wrote, ready, fresh = 0, 0, 0
    for cand in found:
        have = by_name(conn, cand[0])
        if have:
            got, soon = seen_again(conn, have[0], Seen(lc.run_id, cand[0]))
            wrote, ready = wrote + got, ready + soon
        elif fresh < NEW_PER_RUN:
            fresh += 1
            wrote += insert(conn, lc.run_id, cand)
    return wrote, ready, refused


def run(conn, lc) -> StageResult:
    notes: list = []
    wrote, ready, refused = _mine(conn, lc)
    wrote += knowledge_routes_bridge.seed_shortcuts_from_routes(
        lc.package, conn, lc.run_id
    )
    woke = knowledge_routes_bridge.activate_seeds(
        conn, knowledge_routes_bridge.RouteRun(lc.package, lc.steps, lc.run_id)
    )
    wrote += woke
    if ready:
        notes.append("%d shortcut candidates gate-ready" % ready)
    if refused:
        notes.append("%d sequences refused (secret)" % refused)
    if woke:
        notes.append("%d route seeds activated" % woke)
    return StageResult("shortcuts", wrote=wrote, notes=notes)
