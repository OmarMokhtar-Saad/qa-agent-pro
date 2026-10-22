"""Bounded in-process store for exploratory coaching sessions.

The MCP server is one long-lived stdio process, so the store needs a size cap,
an idle TTL and a history bound or it grows for the life of the process.
"""

from __future__ import annotations

import time

MAX_SESSIONS = 50
SESSION_TTL_S = 6 * 3600
MAX_HISTORY = 40

SESSIONS: dict[str, dict] = {}


def _evict(now: float) -> None:
    stale = [
        s for s, v in SESSIONS.items() if now - v.get("_touched", now) > SESSION_TTL_S
    ]
    for sid in stale:
        del SESSIONS[sid]
    while len(SESSIONS) > MAX_SESSIONS:
        del SESSIONS[next(iter(SESSIONS))]


def get_session(session_id: str) -> dict | None:
    """The live session (refreshing its idle clock), or None."""
    _evict(time.monotonic())
    sess = SESSIONS.pop(session_id, None)
    if sess is not None:
        sess["_touched"] = time.monotonic()
        SESSIONS[session_id] = sess  # most recently used last
    return sess


def put_session(session_id: str, sess: dict) -> None:
    """Register a session, evicting expired and least-recently-used ones."""
    sess["_touched"] = time.monotonic()
    SESSIONS.pop(session_id, None)
    SESSIONS[session_id] = sess
    _evict(sess["_touched"])


def append_history(sess: dict, role: str, content: str) -> None:
    """Append a turn and keep only the newest ``MAX_HISTORY`` entries."""
    history = sess["history"]
    history.append({"role": role, "content": content})
    del history[:-MAX_HISTORY]
