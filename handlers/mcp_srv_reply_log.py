"""One bounded, scrubbed log line with the text a tool reply told the host (G11)."""

from __future__ import annotations

from config.settings_guard import never_raise
from handlers import _lane
from handlers.mcp_srv_reply_spool import spool
from handlers.mcp_srv_state import logger

#: Longest excerpt of one reply that reaches the log; the rest is counted, not kept.
REPLY_EXCERPT_CHARS = 600


def reply_text(result: object) -> str:
    """The text a handler result carries: a str, a ``(text, ...)`` tuple or content blocks."""
    if isinstance(result, str):
        return result
    if isinstance(result, tuple) and result:
        return reply_text(result[0])
    if isinstance(result, list):
        return "\n".join(str(getattr(b, "text", "")) for b in result)
    return ""


def excerpt(text: str) -> str:
    """The scrubbed, one-line, size-bounded excerpt of *text* ('' when it has none)."""
    if _lane.scrub_render is None:  # a dist without the mobile lane has no scrubber
        return ""
    clean = _lane.scrub_render.scrub_text(
        _lane.act_scrub.scrub(text[: REPLY_EXCERPT_CHARS * 4])
    )
    short = " ".join(clean.split())[:REPLY_EXCERPT_CHARS]
    return short + (f" ...[{len(text)} chars]" if len(text) > len(short) else "")


def log_reply(name: str, result: object) -> object:
    """Log what *name* told the host and hand *result* back; never raises, never leaks."""
    with never_raise(logger, "tool reply log failed"):
        text = reply_text(result)
        line = excerpt(text)
        if line:
            more = spool(name, text) if len(text) > REPLY_EXCERPT_CHARS else ""
            logger.info("tool reply %s: %s%s", name, line, more)
    return result
