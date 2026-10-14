"""Duplicate-call cache: a retry within the window replays the earlier reply."""

from __future__ import annotations

import time

from handlers import mcp_srv_state as state
from handlers.mcp_srv_state import (
    _GENERATE_DUP_WINDOW_S,
    _RECENT_CALL_TTL_S,
    _RECENT_CALLS_LOCK,
)


def _recent_call_cached(key, ttl_s: float = _RECENT_CALL_TTL_S):
    """Return the cached result for key if still within ttl_s, else None.
    Never raises."""
    with _RECENT_CALLS_LOCK:
        entry = state.recent_calls().get(key)
        if not entry:
            return None
        result, at = entry
        if time.monotonic() - at > ttl_s:
            state.recent_calls().pop(key, None)
            return None
        return result


def _recent_call_store(key, result) -> None:
    """Store result under key with the current time, for _recent_call_cached
    to serve back within the TTL. Drops every entry older than the longest
    window first, so the cache cannot grow for the life of the process."""
    now = time.monotonic()
    horizon = max(_RECENT_CALL_TTL_S, _GENERATE_DUP_WINDOW_S)
    with _RECENT_CALLS_LOCK:
        for stale in [
            k for k, (_, at) in state.recent_calls().items() if now - at > horizon
        ]:
            del state.recent_calls()[stale]
        state.recent_calls()[key] = (result, now)


_DUP_REPLY_PREFIX = (
    "Already done: an identical `qa_generate_test_cases` call "
    "finished moments ago, so nothing was re-run. Its reply "
    "follows; continue from its prep_id.\n\n"
)


def _generate_cache_key(params: dict) -> tuple:
    """The duplicate-call key of a qa_generate_test_cases call: every argument
    but `stage_token`, with `capture_ids` made hashable."""
    return (
        "qa_generate_test_cases",
        params["feature_or_url"],
        params["proceed_anyway"],
        params["jira_content_json"],
        params["source_plan"],
        params["attached_image_count"],
        tuple(params["capture_ids"] or ()),
        params["image_gate_ack"],
        params["image_carry_ack"],
    )
