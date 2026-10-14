"""Moved from handlers (refactor P2): _elicit."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise

logger = logging.getLogger("tools.mcp_handlers")


# --------------------------------------------------------------------------- #
# Per-process, per-client elicitation gate (2026-08-21)                        #
# --------------------------------------------------------------------------- #
#
# v1.56.2 (866ae959) made a TIMED-OUT dialog skip the remaining dialogs of the
# SAME tool call. The live TICKET-5645 run (pid 58542) then showed the next call
# paying the full 55s again, and the two declines landing 68/72 ms AFTER the
# call had already returned -- the client resolved them at turn end, so the
# tester never saw them. No timeout value fixes that; only not asking does.
#
# So the observation is lifted from per-CALL to per-PROCESS, keyed by the MCP
# client name from the initialize handshake. Deliberately NOT a hardcoded
# blocklist: the same client is on record as rendering dialogs in other runs,
# and a static list would silently remove a working path from testers who do
# answer. This record is EARNED at runtime and self-correcting in both
# directions -- an answered (or in-time declined) dialog clears it.
#
# NOT persisted. A wrong verdict must not survive a restart, and an install
# shared by three clients must not have one client's verdict affect another.
# Process lifetime is the right scope, which is why this is a module dict and
# not a row in data/.
#
# NOT a settings flag either (CLAUDE.md flag policy): a per-install toggle has
# no right answer here, and the behaviour is self-correcting.

# Two, not one. With the v1.56.2 per-call fix a single tool call produces at
# most ONE timeout, so two strikes means two SEPARATE calls -- ~110s worst case
# before a client is gated. One strike would let a single slow human cost their
# whole session's dialogs. Two consecutive timeouts with zero answers in
# between is the signal.
_ELICIT_GATE_STRIKES = 2

# client name (lower-cased, from the handshake) -> CONSECUTIVE timeouts.
# Any answer resets the entry to 0, so this counts a run, not a lifetime total.
_elicit_client_strikes: dict[str, int] = {}


# The client the current process is talking to. An MCP stdio server serves one
# client, but the key is still per NAME so a shared ~/qa-agent-pro install can
# never have one editor's verdict gate another's.
elicit_client_current = {"name": ""}


def _reset_elicit_client_state() -> None:
    """Test-only reset. Used by an autouse conftest fixture, because the record
    is module-global and a leaked strike would gate the "" client for whatever
    test runs next."""
    _elicit_client_strikes.clear()
    elicit_client_current["name"] = ""


def note_elicit_client(name) -> None:
    """Record which client this process is serving (mcp_server._note_client
    forwards the initialize handshake's clientInfo.name). Never raises."""
    with never_raise(logger, "elicit client note failed", exc_info=True):
        elicit_client_current["name"] = str(name or "").strip().lower()


def _elicit_client_key(name=None) -> str:
    with never_raise():
        if name is None:
            return elicit_client_current.get("name", "") or ""
        return str(name or "").strip().lower()
    return ""


def elicit_client_gated(name=None) -> bool:
    """True when THIS client has timed out _ELICIT_GATE_STRIKES dialogs in a row
    with no answer in between, i.e. elicitation on it has never been answerable
    in this process. Read by mcp_server._make_chooser / _make_asker, which then
    hand back None and let the existing markdown-menu fallback run.
    Never raises -- a read failure means "not gated", so the dialog is asked."""
    with never_raise(logger, "elicit gate read failed", exc_info=True):
        return _elicit_client_strikes.get(_elicit_client_key(name), 0) >= (
            _ELICIT_GATE_STRIKES
        )
    return False


def note_elicit_timeout(name=None) -> None:
    """One dialog timed out: add a strike, and say so ONCE at INFO when the gate
    closes. INFO rather than DEBUG for the same reason elicit_choice's timeout
    logs at WARNING -- the installed log runs at INFO and a silent capability
    downgrade is undiagnosable from the log file. Never raises."""
    with never_raise(logger, "elicit strike failed", exc_info=True):
        key = _elicit_client_key(name)
        strikes = _elicit_client_strikes.get(key, 0) + 1
        _elicit_client_strikes[key] = strikes
        if strikes == _ELICIT_GATE_STRIKES:
            logger.info(
                "mcp elicitation gated for client %r after %d consecutive "
                "unanswered dialogs -- no further dialogs this process; the "
                "markdown menu is used instead. Any answered dialog clears it.",
                key or "<unknown>",
                strikes,
            )


def note_elicit_answered(name=None) -> None:
    """A dialog was ANSWERED, or DECLINED in time -- either way the tester saw it,
    so the transport works and the strike run is over. This is the half that
    makes the gate self-correcting: a client that answers is never gated, no
    matter how many earlier timeouts it accumulated. Never raises."""
    with never_raise(logger, "elicit answer note failed", exc_info=True):
        _elicit_client_strikes[_elicit_client_key(name)] = 0
