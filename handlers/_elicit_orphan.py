"""Moved from handlers (refactor P2): _elicit."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise

logger = logging.getLogger("tools.mcp_handlers")


# A3 (2026-08-21, TICKET-5138): the SDK loggers that report an elicitation answer
# arriving AFTER we abandoned the dialog. Attached individually because the stdlib
# does not consult an ANCESTOR logger's filters.
_ORPHAN_RESPONSE_LOGGERS = ("mcp.server.lowlevel.server", "mcp.shared.session")
_ORPHAN_RESPONSE_NEEDLE = "unknown request id"
_orphan_filter_installed: dict = {"done": False, "filter": None}


class _OrphanElicitResponseFilter(logging.Filter):
    """Demote a late "unknown request ID" record from ERROR to DEBUG.

    asyncio.wait_for CANCELS the pending ctx.elicit, so a tester who answers the
    collapsed Cursor panel late sends a JSON-RPC response for a request id the
    session no longer tracks, and the SDK logs "Received exception from stream:
    Received response with an unknown request ID" at ERROR. On the live run that
    arrived 24 ms after the tool returned. Nothing is broken and there is nothing
    for a tester or an operator to do: it is a designed consequence of OUR bound,
    and it was the loudest line in the log.

    DEMOTED rather than dropped, so a genuine protocol desync is still findable at
    DEBUG. Handler level checks read record.levelno, so the installed INFO log no
    longer shows it. Returns True always -- no other filter or handler behaviour
    changes -- and never raises. Accepted cost: a real desync in a process that has
    already abandoned a dialog is reported at DEBUG."""

    def filter(self, record) -> bool:
        with never_raise():
            if _ORPHAN_RESPONSE_NEEDLE in str(record.getMessage()).lower():
                record.levelno = logging.DEBUG
                record.levelname = "DEBUG"
                record.exc_info = None
                record.exc_text = None
        return True


def _suppress_orphan_elicit_logs() -> None:
    """Arm the orphan-response filter once per process, at the moment a dialog is
    ABANDONED.

    Lazily and not at import, deliberately: before this process has abandoned a
    dialog an "unknown request ID" really IS unexplained and must stay at ERROR.
    Idempotent; never raises."""
    if _orphan_filter_installed["done"]:
        return
    with never_raise(logger, "orphan elicit filter install failed", exc_info=True):
        _filter = _OrphanElicitResponseFilter()
        for _name in _ORPHAN_RESPONSE_LOGGERS:
            logging.getLogger(_name).addFilter(_filter)
        _orphan_filter_installed["filter"] = _filter
        _orphan_filter_installed["done"] = True


def _rearm_orphan_elicit_logs() -> None:
    """DISARM the orphan filter after a dialog was answered (or declined).

    Without this the filter, once armed, demotes every unknown-request-ID record
    for the rest of the process -- including a genuine protocol desync minutes
    later on a client that has since started answering dialogs. An answered or
    declined dialog is proof the transport is live, which is the same signal
    note_elicit_answered uses to clear the per-client strike record, so the two
    clear together. Idempotent; never raises."""
    _filter = _orphan_filter_installed.get("filter")
    if _filter is None:
        return
    with never_raise(logger, "orphan elicit filter re-arm failed", exc_info=True):
        for _name in _ORPHAN_RESPONSE_LOGGERS:
            logging.getLogger(_name).removeFilter(_filter)
    _orphan_filter_installed["filter"] = None
    _orphan_filter_installed["done"] = False
