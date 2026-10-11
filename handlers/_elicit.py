"""Moved from handlers (refactor P2): _elicit."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable, Optional

from handlers import _common
from tools.device_manager import list_devices
from tools.suite_store import list_recent_suites

logger = logging.getLogger("tools.mcp_handlers")

# 2026-08-10 (K1): how long ANY single MCP elicitation may hold a tool call open.
# Named for the export dialog until this batch, because that was the only bounded
# site; every _elicit_text/_elicit_choice site is bounded now, so the export-only
# wording would have been false. The export prompt still interpolates int() of it,
# so the wording and the bound cannot drift apart. A module global so tests can
# shrink it.
#
# WHY THIS EXISTS AT ALL: ctx.elicit renders in Cursor as a COLLAPSED
# "User Input Required" panel with a REQUIRED Value* field. The tester never sees
# the question, so an unanswered dialog held the whole tool call until the client
# killed it at its ~120s idle timeout.
# A1 (2026-08-21, TICKET-5138): 20.0, was 55.0. A live Cursor run spent 55023 ms of a
# 55023 ms qa_prepare_test_cases call waiting on ONE dialog -- ~100% of the tool
# call -- and the answer still arrived only AFTER the call had returned. 55s was
# chosen in K1 for exactly one property, "worst case stays under the client's
# measured ~120s idle kill"; it was never justified as long enough for a tester to
# ANSWER, and in Cursor it cannot be: the dialog renders as a COLLAPSED
# "User Input Required" panel with a required Value* field, so the dominant failure
# is the tester never SEEING the question. Extra seconds do not cure not-noticing;
# they only delay the fallback that is visible -- the markdown menu in the reply
# text, which the host relays into the chat.
#
# WHY 20.0 and not an arbitrary smaller number: 20.0 IS _ELICIT_FLOOR_S below. The
# floor already asks a call's FIRST dialog for at least 20s even on a spent budget,
# so any value under 20 would make this constant a bound the floor routinely
# overrides -- a number that does not describe the behaviour, which is the class of
# untrue text this whole area exists to remove -- and would break the pinned
# invariant _ELICIT_FLOOR_S <= _ELICIT_TIMEOUT_S. At 20.0 the three-dialog wizard's
# worst case is 3x20 = 60s inside the unchanged 80s per-call budget, still well
# under ~120s, and one blind dialog costs a tester 20s instead of 55s. A shorter
# bound also makes LATE orphan answers more frequent, which is exactly what
# _suppress_orphan_elicit_logs handles.
_ELICIT_TIMEOUT_S = 20.0

# Per-CALL budget. Dialogs chain sequentially inside one tool call (the image gate
# asks twice, the wizard up to three times), so a per-dialog bound alone still let
# one call run 110-220s and die at the client's idle timeout. 80.0 + one
# _ELICIT_FLOOR_S is 100s worst case from tool entry -- under ~120s with margin.
_ELICIT_CALL_BUDGET_S = 80.0

# The FIRST dialog of a call is always asked, for at least this long, even when the
# budget is already spent. Without it a dialog sitting behind minutes of work --
# _auto_export_xlsx runs at the END of a full 8-category generation -- would be
# skipped silently, and on a legacy install the tester's answer is the only signal
# there is. Bounded: max(min(_ELICIT_TIMEOUT_S, remaining), _ELICIT_FLOOR_S) never
# exceeds _ELICIT_TIMEOUT_S.
_ELICIT_FLOOR_S = 20.0


# --------------------------------------------------------------------------- #
# Elicitation helpers (never-raise; degrade to UNAVAILABLE -> markdown menu)   #
# --------------------------------------------------------------------------- #


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

# A DECLINE that comes back faster than a human could have READ the dialog
# is not a dismissal -- it is a client auto-declining because there is no
# one there to answer. Observed live on 2026-08-31 in a headless
# `cursor-agent -p` session: the image gate was elicited, instantly
# declined, and re-asked as markdown, costing a full round trip on the
# FIRST Jira call of every session. It never stopped, because each instant
# decline reset the very strike record that would have gated the dialog --
# note_elicit_answered treats DECLINED as proof the tester saw it.
#
# 1.5s is deliberately generous: a real human needs seconds to read a
# multi-option prompt and click, so this cannot mistake a fast tester for a
# robot, and being wrong costs only that the gate stays open one more
# dialog. Two such declines in a row still gate; a single one never does.
_INSTANT_DECLINE_S = 1.5

# client name (lower-cased, from the handshake) -> CONSECUTIVE timeouts.
# Any answer resets the entry to 0, so this counts a run, not a lifetime total.
_elicit_client_strikes: dict[str, int] = {}

# The client the current process is talking to. An MCP stdio server serves one
# client, but the key is still per NAME so a shared ~/qa-agent-pro install can
# never have one editor's verdict gate another's.
_elicit_client_current = {"name": ""}


def _reset_elicit_client_state() -> None:
    """Test-only reset. Used by an autouse conftest fixture, because the record
    is module-global and a leaked strike would gate the "" client for whatever
    test runs next."""
    _elicit_client_strikes.clear()
    _elicit_client_current["name"] = ""


def note_elicit_client(name) -> None:
    """Record which client this process is serving (mcp_server._note_client
    forwards the initialize handshake's clientInfo.name). Never raises."""
    try:
        _elicit_client_current["name"] = str(name or "").strip().lower()
    except Exception:
        logger.debug("elicit client note failed", exc_info=True)


# --------------------------------------------------------------------------- #
# v1.97.0 cursor-hardening (item 3b/6a/6b): provenance state, kept SEPARATE
# from _elicit_client_current so elicitation gating and provenance can evolve
# independently.
# --------------------------------------------------------------------------- #
_provenance_client = {"name": "", "version": ""}

# The documented hosts, kept in step with jira_mcp._detect_client_key's
# four clients (cursor / gemini-cli / claude-desktop / claude-code);
# "claude" covers the last two. Matched as a case-insensitive substring
# so a versioned or vendor-prefixed clientInfo.name still matches.
_KNOWN_EDITOR_SUBSTRINGS = ("cursor", "claude", "gemini")


def note_provenance_client(name, version: str = "") -> None:
    """Record which client (name + version) this process is serving, for
    stamping onto every saved suite. Mirrors note_elicit_client's shape.
    Never raises."""
    try:
        _provenance_client["name"] = str(name or "").strip()
        _provenance_client["version"] = str(version or "").strip()
    except Exception:
        logger.debug("provenance client note failed", exc_info=True)


def _known_editor_host(name: str) -> bool:
    """True when name matches one of the documented hosts. Empty or
    unrecognized -- e.g. a side-channel process's own spawned
    mcp.ClientSession -- is False. Never raises."""
    try:
        low = str(name or "").strip().lower()
        if not low:
            return False
        return any(sub in low for sub in _KNOWN_EDITOR_SUBSTRINGS)
    except Exception:
        return False


def _dispatch_provenance() -> dict:
    """{"dispatch", "client_name", "client_version", "known_editor"} for the
    suite about to be saved. Reaching this at all already proves
    dispatch_guard.require_dispatched passed for THIS call -- a guard-less
    attempt raises before any handler body runs -- so every suite saved
    through this path carries "dispatch": "mcp_tracked". Never raises."""
    try:
        name = _provenance_client.get("name", "")
        version = _provenance_client.get("version", "")
        return {
            "dispatch": "mcp_tracked",
            "client_name": name,
            "client_version": version,
            "known_editor": _known_editor_host(name),
        }
    except Exception:
        logger.debug("_dispatch_provenance failed", exc_info=True)
        return {
            "dispatch": "mcp_tracked",
            "client_name": "",
            "client_version": "",
            "known_editor": False,
        }


def _union_provenance(rows: "list | None") -> dict:
    """Union of every ``qa_submit_category`` row's OWN client_name (Step 2
    stamps it into ``row["payload"]["client_name"]``) with the finalizing
    call's own client. A2/N2, the headline fix: before this,
    ``qa_submit_suite`` stamped ``known_editor`` from ONLY the finalizing
    process, so eight categories submitted by an unrecognised side-channel
    client and finalized by a known editor saved ``known_editor: True`` and
    dropped every contributor on the floor. ``known_editor`` is True here
    only when EVERY contributor (each row's client, plus this call's own) is
    a known editor host. Never raises -- any failure degrades to the single
    finalizing client, the pre-fix behaviour."""
    try:
        _base = _dispatch_provenance()
        _clients: list = []
        _seen: set = set()
        for _row in rows or []:
            _payload = (_row or {}).get("payload") or {}
            _name = str(_payload.get("client_name") or "").strip()
            if _name and _name not in _seen:
                _seen.add(_name)
                _clients.append(_name)
        _final_name = str(_base.get("client_name") or "").strip()
        if _final_name and _final_name not in _seen:
            _seen.add(_final_name)
            _clients.append(_final_name)
        _known = (
            all(_known_editor_host(c) for c in _clients)
            if _clients
            else bool(_base.get("known_editor"))
        )
        return {
            "dispatch": _base.get("dispatch", "mcp_tracked"),
            "client_name": _final_name,
            "client_version": _base.get("client_version", ""),
            "known_editor": bool(_known),
            "contributing_clients": _clients,
        }
    except Exception:
        logger.debug("_union_provenance failed", exc_info=True)
        return _dispatch_provenance()


def _unrecognized_client_note(provenance: dict, *, context: str) -> str:
    """The A3/A4 warn-only banner: "" whenever every contributor is a known
    editor host, else a loud "> warning" line naming every unrecognised
    client. Ships ON, no flag (A3 owner decision) -- this NEVER gates a
    return; the caller's submit always succeeds regardless of what this
    returns. Reused for both the per-call ``qa_submit_category`` reply and
    the finalize ``qa_submit_suite`` reply/session-provenance section, so
    the two never drift. Never raises."""
    try:
        provenance = provenance or {}
        if provenance.get("known_editor"):
            return ""
        _clients = list(provenance.get("contributing_clients") or [])
        if not _clients:
            _solo = str(provenance.get("client_name") or "unknown")
            _clients = [_solo]
        _label = ", ".join(f"`{c}`" for c in _clients if c) or "`unknown`"
        _scope = "This category submission" if context == "category" else "This suite"
        return (
            "> \u26a0\ufe0f **" + _scope + " was recorded from an MCP client "
            "this server does not recognise** (" + _label + "). If you did "
            "not expect that, a process may be calling this server's tools "
            "outside your editor's own MCP session. Submission was NOT "
            "blocked -- this is a disclosure, not a refusal.\n\n"
        )
    except Exception:
        logger.debug("_unrecognized_client_note failed", exc_info=True)
        return ""


def _provenance_gen_note_detail(provenance: dict) -> str:
    """The Generation Notes workbook row text for session provenance -- ""
    when every contributor is a known editor host (the sheet gets no row),
    else the same fact the chat warning discloses, phrased for a cell the
    tester reads later rather than mid-chat. Never raises."""
    try:
        provenance = provenance or {}
        if provenance.get("known_editor"):
            return ""
        _clients = list(provenance.get("contributing_clients") or [])
        if not _clients:
            _solo = str(provenance.get("client_name") or "unknown")
            _clients = [_solo]
        _label = ", ".join(_clients) or "unknown"
        return (
            "This suite was recorded from an MCP client this server does "
            f"not recognise ({_label}). If this was not expected, a process "
            "may be calling this server's tools outside the editor's own MCP "
            "session."
        )
    except Exception:
        logger.debug("_provenance_gen_note_detail failed", exc_info=True)
        return ""


def _elicit_client_key(name=None) -> str:
    try:
        if name is None:
            return _elicit_client_current.get("name", "") or ""
        return str(name or "").strip().lower()
    except Exception:
        return ""


def elicit_client_gated(name=None) -> bool:
    """True when THIS client has timed out _ELICIT_GATE_STRIKES dialogs in a row
    with no answer in between, i.e. elicitation on it has never been answerable
    in this process. Read by mcp_server._make_chooser / _make_asker, which then
    hand back None and let the existing markdown-menu fallback run.
    Never raises -- a read failure means "not gated", so the dialog is asked."""
    try:
        return _elicit_client_strikes.get(_elicit_client_key(name), 0) >= (
            _ELICIT_GATE_STRIKES
        )
    except Exception:
        logger.debug("elicit gate read failed", exc_info=True)
        return False


def note_elicit_timeout(name=None) -> None:
    """One dialog timed out: add a strike, and say so ONCE at INFO when the gate
    closes. INFO rather than DEBUG for the same reason _elicit_choice's timeout
    logs at WARNING -- the installed log runs at INFO and a silent capability
    downgrade is undiagnosable from the log file. Never raises."""
    try:
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
    except Exception:
        logger.debug("elicit strike failed", exc_info=True)


def note_elicit_answered(name=None) -> None:
    """A dialog was ANSWERED, or DECLINED in time -- either way the tester saw it,
    so the transport works and the strike run is over. This is the half that
    makes the gate self-correcting: a client that answers is never gated, no
    matter how many earlier timeouts it accumulated. Never raises."""
    try:
        _elicit_client_strikes[_elicit_client_key(name)] = 0
    except Exception:
        logger.debug("elicit answer note failed", exc_info=True)


def _note_elicit_outcome(result, elapsed_s=None) -> None:
    """Feed one completed dialog result into the per-client record.

    ONLY a CHOSEN or DECLINED result counts as evidence that the tester saw the
    dialog. UNAVAILABLE does not: it is what a raising ctx.elicit degrades to,
    which says nothing about the human, and clearing strikes on it would make a
    client whose transport raises every time permanently ungatable.

    2026-09-01: with ONE exception, and it is the reason the gate never closed
    on a headless client. A DECLINED that came back in under
    ``_INSTANT_DECLINE_S`` is a strike, not an answer -- nobody read it. A
    CHOSEN always clears, however fast: a value came back, so something
    answered. *elapsed_s* is None from callers that did not time the round
    trip, and those keep the old behaviour exactly.
    Never raises."""
    try:
        _status = getattr(result, "status", None)
        if (
            _status == _common.DECLINED
            and elapsed_s is not None
            and elapsed_s < _INSTANT_DECLINE_S
        ):
            logger.debug(
                "mcp elicit declined in %.2fs -- too fast to have been read; "
                "counting a strike rather than an answer",
                elapsed_s,
            )
            note_elicit_timeout()
            return
        if _status in (_common.CHOSEN, _common.DECLINED):
            note_elicit_answered()
            # A3 re-arm (2026-08-21). The status carve-out is NOT widened: it is
            # load-bearing and separately pinned -- an UNAVAILABLE result is a
            # raising transport, not an answer. A live transport means a later
            # unknown-request-ID record is unexplained again, so the demotion
            # filter is removed here.
            _rearm_orphan_elicit_logs()
    except Exception:
        logger.debug("elicit outcome note failed", exc_info=True)


def _elicit_wait_s(cb) -> float | None:
    """How long the NEXT dialog on *cb* may wait, or None when the per-call budget
    is already spent and this is not the call's first dialog.

    The budget holder is attached to the callback object by
    ``mcp_server._make_elicitors`` -- a duck-typed private attribute rather than a
    parameter, so none of the ~15 handler signatures had to change. Both ends are
    cross-referenced: see ``_make_elicitors`` in mcp_server.py. A callback without
    one (tests, .claude/local/mcp_matrix.py) simply gets per-dialog bounding, which
    fails safe.

    The FIRST dialog of a call is floored at _ELICIT_FLOOR_S even on an exhausted
    budget, so a dialog behind minutes of work is still asked. ``asked`` makes
    "first dialog" a recorded fact rather than something inferred from the clock.

    2026-08-21: a dialog that TIMED OUT marks the shared holder ``dead``, and a
    dead transport skips every LATER dialog of the same call. The live TICKET-5645
    run spent 80.0s of an 80.02s tool call re-asking a transport that had already
    proven unresponsive: _elicit_choice returned timed_out=True and NOTHING read
    it. Checked AFTER the first-dialog floor above, so that guarantee is
    unchanged, and per-CALL because the holder is per-call.
    Never raises."""
    try:
        budget = getattr(cb, "_elicit_budget", None)
        if not isinstance(budget, dict):
            return _ELICIT_TIMEOUT_S
        remaining = float(budget.get("deadline") or 0.0) - time.monotonic()
        if not budget.get("asked"):
            return max(min(_ELICIT_TIMEOUT_S, remaining), _ELICIT_FLOOR_S)
        if budget.get("dead"):
            return None
        if remaining <= 0:
            return None
        return min(_ELICIT_TIMEOUT_S, remaining)
    except Exception:
        logger.debug("elicit budget read failed", exc_info=True)
        return _ELICIT_TIMEOUT_S


def _elicit_dead(cb) -> bool:
    """True when an EARLIER dialog of this same call timed out.

    Read only for WORDING, by _log_elicit_skip. The skip DECISION stays in
    _elicit_wait_s, which remains the single owner of it. Never raises."""
    try:
        budget = getattr(cb, "_elicit_budget", None)
        return bool(isinstance(budget, dict) and budget.get("dead"))
    except Exception:  # pragma: no cover - defensive; a read must never raise
        logger.debug("elicit dead read failed", exc_info=True)
        return False


def _log_elicit_skip(what: str, cb, message: str) -> None:
    """Log a SKIPPED dialog with its REAL cause.

    A2 (2026-08-21, TICKET-5138): both helpers logged "per-call elicitation budget
    spent" for every skip. On the live run the choice dialog timed out at
    21:06:12,506 and its own elicit_text fallback was skipped 2 ms later -- 2 ms
    into an 80s budget, so nothing was spent; the cause was the dead-mark. A
    timed-out prompt must not be ACCOUNTED as its own fallback's budget spend.

    The fallback is still skipped, deliberately: a transport that just proved it
    does not answer within the bound renders the second dialog just as invisibly,
    so re-asking buys another blind wait and no answer. What reaches the tester is
    the markdown menu the call site already relays. Never raises."""
    if _elicit_dead(cb):
        logger.warning(
            "mcp %s skipped for %r -- an earlier dialog in this call timed out, "
            "so this client is not answering (per-call budget NOT spent)",
            what,
            message,
        )
    else:
        logger.warning(
            "mcp %s skipped for %r -- per-call elicitation budget spent",
            what,
            message,
        )


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
        try:
            if _ORPHAN_RESPONSE_NEEDLE in str(record.getMessage()).lower():
                record.levelno = logging.DEBUG
                record.levelname = "DEBUG"
                record.exc_info = None
                record.exc_text = None
        except Exception:  # pragma: no cover - defensive; a filter must not raise
            return True
        return True


def _suppress_orphan_elicit_logs() -> None:
    """Arm the orphan-response filter once per process, at the moment a dialog is
    ABANDONED.

    Lazily and not at import, deliberately: before this process has abandoned a
    dialog an "unknown request ID" really IS unexplained and must stay at ERROR.
    Idempotent; never raises."""
    if _orphan_filter_installed["done"]:
        return
    try:
        _filter = _OrphanElicitResponseFilter()
        for _name in _ORPHAN_RESPONSE_LOGGERS:
            logging.getLogger(_name).addFilter(_filter)
        _orphan_filter_installed["filter"] = _filter
        _orphan_filter_installed["done"] = True
    except Exception:  # pragma: no cover - defensive; logging is not a feature
        logger.debug("orphan elicit filter install failed", exc_info=True)


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
    try:
        for _name in _ORPHAN_RESPONSE_LOGGERS:
            logging.getLogger(_name).removeFilter(_filter)
    except Exception:  # pragma: no cover - defensive; logging is not a feature
        logger.debug("orphan elicit filter re-arm failed", exc_info=True)
    _orphan_filter_installed["filter"] = None
    _orphan_filter_installed["done"] = False


def _mark_elicit_asked(cb) -> None:
    """Record that this call has now shown a dialog, so the floor applies once."""
    try:
        budget = getattr(cb, "_elicit_budget", None)
        if isinstance(budget, dict):
            budget["asked"] = True
    except Exception:
        logger.debug("elicit budget mark failed", exc_info=True)


def _mark_elicit_dead(cb) -> None:
    """Record that a dialog on *cb* TIMED OUT, so later dialogs of the SAME call
    are skipped instead of re-waiting on a transport that has proven unresponsive.

    ONLY a timeout sets this. A DECLINED dialog means the tester saw the question
    and dismissed it, and an answered one means the transport works -- both must
    leave the next dialog askable. Same never-raising discipline as
    _mark_elicit_asked, and the same per-call holder, so deadness never leaks
    across tool calls."""
    # A3 (2026-08-21): abandoning a dialog is exactly when a late answer becomes
    # EXPECTED, so this is where the orphan-response filter is armed. One site
    # covers both helpers -- every timeout branch already calls this function.
    _suppress_orphan_elicit_logs()
    try:
        budget = getattr(cb, "_elicit_budget", None)
        if isinstance(budget, dict):
            budget["dead"] = True
    except Exception:
        logger.debug("elicit budget dead-mark failed", exc_info=True)


async def _elicit_choice(
    choose: _common.ChooseCb, message: str, options: list
) -> _common.ChoiceResult:
    """Run one elicitation round, degrading to UNAVAILABLE on a missing callback
    or any transport error (e.g. a client without elicitation support).

    2026-08-10 (K1): also bounded -- by _ELICIT_TIMEOUT_S per dialog and by the
    per-call budget. An unanswered dialog used to hold the whole tool call until the
    client killed it at ~120s."""
    if choose is None:
        return _common.ChoiceResult(_common.UNAVAILABLE)
    wait_s = _elicit_wait_s(choose)
    if wait_s is None:
        # A2: the cause is logged by _log_elicit_skip, which distinguishes a spent
        # budget from a transport this call already proved unresponsive.
        _log_elicit_skip("elicit_choice", choose, message)
        return _common.ChoiceResult(_common.UNAVAILABLE, budget_skipped=True)
    _mark_elicit_asked(choose)
    _started = time.monotonic()
    try:
        result = await asyncio.wait_for(choose(message, list(options)), timeout=wait_s)
    except asyncio.TimeoutError:
        # BEFORE the bare `except Exception` -- asyncio.TimeoutError is an Exception
        # subclass on every version this project supports (>=3.10), so the ordering
        # is load-bearing. WARNING, not DEBUG: the installed log runs at INFO and a
        # silent timeout is undiagnosable from the log file.
        # Says WHAT HAPPENS NEXT (fix round 3, item 6): the caller falls back
        # to its text menu, and this log is where that is diagnosed from.
        logger.warning(
            "mcp elicit_choice timed out after %.0fs for %r; "
            "falling back to the text menu",
            wait_s,
            message,
        )
        # ONLY here. A proven-dead transport must not cost the rest of this call
        # another full dialog bound. The bare `except Exception` below, the
        # DECLINED result and the answered result all leave the next dialog
        # askable -- a tester who dismisses dialog 1 must still get dialog 2.
        _mark_elicit_dead(choose)
        # ...and one strike against the CLIENT, which outlives this call. Two in a
        # row with no answer between them gate it for the process.
        note_elicit_timeout()
        return _common.ChoiceResult(_common.UNAVAILABLE, timed_out=True)
    except Exception:
        logger.debug("mcp elicit_choice failed for %r", message, exc_info=True)
        return _common.ChoiceResult(_common.UNAVAILABLE)
    if isinstance(result, _common.ChoiceResult):
        _note_elicit_outcome(result, time.monotonic() - _started)
        return result
    return _common.ChoiceResult(_common.UNAVAILABLE)


# The free-text sibling of ChooseCb: ``ask_text(message) -> ChoiceResult`` where
# CHOSEN carries the typed text. Provided by the transport adapter
# (mcp_server._make_asker); None when elicitation is off/unsupported.
AskCb = Optional[Callable[[str], Awaitable["_common.ChoiceResult"]]]


async def _elicit_text(ask_text: AskCb, message: str) -> _common.ChoiceResult:
    """One free-text elicitation round; UNAVAILABLE without a callback or on
    any transport error (mirrors _elicit_choice), and bounded the same way -- see
    _elicit_wait_s and mcp_server._make_elicitors."""
    if ask_text is None:
        return _common.ChoiceResult(_common.UNAVAILABLE)
    wait_s = _elicit_wait_s(ask_text)
    if wait_s is None:
        # A2: see the sibling in _elicit_choice.
        _log_elicit_skip("elicit_text", ask_text, message)
        return _common.ChoiceResult(_common.UNAVAILABLE, budget_skipped=True)
    _mark_elicit_asked(ask_text)
    _started = time.monotonic()
    try:
        result = await asyncio.wait_for(ask_text(message), timeout=wait_s)
    except asyncio.TimeoutError:
        logger.warning("mcp elicit_text timed out after %.0fs for %r", wait_s, message)
        # ONLY the timeout branch -- see the sibling in _elicit_choice.
        _mark_elicit_dead(ask_text)
        note_elicit_timeout()  # see the sibling in _elicit_choice
        return _common.ChoiceResult(_common.UNAVAILABLE, timed_out=True)
    except Exception:
        logger.debug("mcp elicit_text failed for %r", message, exc_info=True)
        return _common.ChoiceResult(_common.UNAVAILABLE)
    if isinstance(result, _common.ChoiceResult):
        _note_elicit_outcome(result, time.monotonic() - _started)
        return result
    return _common.ChoiceResult(_common.UNAVAILABLE)


async def _elicit_suite(choose: _common.ChooseCb) -> _common.ChoiceResult:
    """Offer the most recent stored suites as a choice (non-technical parity
    with Chainlit's recent-suites list). CHOSEN carries the suite_id."""
    result = await list_recent_suites(limit=5)
    rows = result.get("content") or []
    if not rows:
        return _common.ChoiceResult(_common.UNAVAILABLE)
    labels: list = []
    by_label: dict = {}
    for row in rows:
        feature = " ".join((row.get("feature_text") or "untitled").split())[:60]
        label = (
            f"{feature} — {row.get('case_count', 0)} cases "
            f"({str(row.get('suite_id'))[:8]}…)"
        )
        labels.append(label)
        by_label[label] = row.get("suite_id")
    picked = await _elicit_choice(choose, "Which suite?", labels)
    if picked.status == _common.CHOSEN:
        return _common.ChoiceResult(_common.CHOSEN, by_label.get(picked.value or ""))
    return picked


async def _recent_suites_markdown(tool: str) -> str:
    """Markdown fallback: list recent stored suites plus a re-call instruction."""
    result = await list_recent_suites(limit=5)
    rows = result.get("content") or []
    if not rows:
        return (
            "⚠️ Provide the suite_id returned by qa_generate_test_cases "
            "(no stored suites yet — generate one first)."
        )
    lines = ["⚠️ Provide a `suite_id`. Recent suites:", ""]
    for row in rows:
        feature = " ".join((row.get("feature_text") or "untitled").split())[:60]
        lines.append(
            f"- `{row.get('suite_id')}` — {feature} ({row.get('case_count', 0)} cases)"
        )
    lines.append("")
    lines.append(f"Re-call `{tool}` with the chosen `suite_id`.")
    return "\n".join(lines)


def _device_options(devices: list):
    """Build (labels, label->id) for a device elicitation. The label leads with
    the device name and carries the id so distinct devices sharing a name stay
    unambiguous when the selected label is mapped back to an id."""
    labels: list = []
    by_label: dict = {}
    for dev in devices:
        did = dev.get("id")
        label = f"{dev.get('name') or did} ({did})"
        labels.append(label)
        by_label[label] = did
    return labels, by_label


async def _elicit_device(choose: _common.ChooseCb) -> _common.ChoiceResult:
    """Scan live devices and elicit one by name. Returns CHOSEN with the device
    id, or UNAVAILABLE (no callback / no devices / transport error)."""
    result = await list_devices()
    devices = result.get("content") or []
    if not devices:
        return _common.ChoiceResult(_common.UNAVAILABLE)
    labels, by_label = _device_options(devices)
    picked = await _elicit_choice(choose, "Which device?", labels)
    if picked.status == _common.CHOSEN:
        return _common.ChoiceResult(_common.CHOSEN, by_label.get(picked.value or ""))
    return picked


def _elicitation_disabled(choose: _common.ChooseCb, ask_text: AskCb) -> bool:
    """True when beat 1 elicitation is switched off, not merely unsupported."""
    # 2026-08-21: a client GATED by elicit_client_gated() also arrives with both
    # callbacks None, but that is a capability OBSERVATION about the client, not
    # an operator disabling the feature -- and this label is stamped into the
    # mcp_image_gate_beat1 audit row. It therefore keeps the byte-identical
    # "unavailable/unavailable" form, which is exactly what a client that cannot
    # show dialogs already reports; "disabled" stays reserved for the
    # _elicit_enabled() seam it was written for.
    return bool(
        not _common._elicit_enabled()
        or (choose is None and ask_text is None and not elicit_client_gated())
    )
