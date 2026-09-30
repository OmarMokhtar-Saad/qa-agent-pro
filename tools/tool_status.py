"""A per-call degraded-status sink: the facts a tool call must not report as ``ok``.

Fix round 3, item 3. On the Air run a screencap timed out after 30 s and the
call was still logged ``tool qa_mobile_test: ok in 47589 ms``: the handler
turned the failure into a note, returned text, and ``mcp_server._tracked`` --
the one seam every tool passes through -- saw a return and wrote ``ok``.

A device helper that gives up on a timeout calls :func:`mark_timed_out`;
``_tracked`` opens the sink with :func:`begin` and reads it with
:func:`finish`. The sink is a LIST held in a ContextVar, not a value, so a
mark made inside a child task (which runs on a COPY of the context) still
reaches the list the caller opened.

Never raises: a status sink that could fail a tool call would be a worse
defect than the one it reports.
"""

from __future__ import annotations

import contextvars
import logging

logger = logging.getLogger(__name__)

#: Marks kept per call. A poll loop can time out several times in one call;
#: the log line needs the kinds, not every repetition.
MAX_MARKS = 16

_MARKS: contextvars.ContextVar[list | None] = contextvars.ContextVar(
    "qa_tool_status_marks", default=None
)


def begin() -> contextvars.Token | None:
    """Open a fresh sink for THIS call. Returns the token :func:`finish` needs."""
    try:
        return _MARKS.set([])
    except Exception:  # pragma: no cover - defensive
        logger.debug("tool_status.begin failed", exc_info=True)
        return None


def mark_timed_out(what: str) -> None:
    """Record that *what* (``screencap``, ``uiautomator_dump``) timed out.

    A no-op outside a call that opened a sink, so library code may call it
    unconditionally.
    """
    try:
        sink = _MARKS.get()
        if sink is not None and len(sink) < MAX_MARKS:
            sink.append(str(what or "unknown")[:40])
    except Exception:  # pragma: no cover - defensive
        logger.debug("tool_status.mark_timed_out failed", exc_info=True)


def finish(token: contextvars.Token | None) -> list[str]:
    """The marks this call collected, and the sink closed. Never raises."""
    try:
        out = list(_MARKS.get() or [])
    except Exception:  # pragma: no cover - defensive
        out = []
    if token is not None:
        try:
            _MARKS.reset(token)
        except Exception:  # pragma: no cover - defensive
            # A token from another context: leave the var alone rather than
            # fail the call. The next begin() replaces it anyway.
            logger.debug("tool_status.finish reset failed", exc_info=True)
    return out
