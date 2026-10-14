"""Moved from handlers (refactor P2): _elicit."""

from __future__ import annotations

import sys

from handlers import _common, _elicit_host
from handlers._elicit_budget import (  # noqa: F401
    _elicit_dead,
    _mark_elicit_dead,
    elicit_wait_s,
)
from handlers._elicit_gate import (  # noqa: F401
    _ELICIT_GATE_STRIKES,
    _elicit_client_key,
    _elicit_client_strikes,
    _reset_elicit_client_state,
    elicit_client_current,
    elicit_client_gated,
    note_elicit_answered,
    note_elicit_client,
    note_elicit_timeout,
)
from handlers._elicit_opts import (  # noqa: F401
    AskCb,
    _device_options,
    _elicitation_disabled,
    _suite_options,
)
from handlers._elicit_orphan import (  # noqa: F401
    _ORPHAN_RESPONSE_LOGGERS,
    _orphan_filter_installed,
    _OrphanElicitResponseFilter,
)
from handlers._elicit_outcome import _note_elicit_outcome  # noqa: F401
from handlers._elicit_prov import (  # noqa: F401
    dispatch_provenance,
    note_provenance_client,
    union_provenance,
)
from handlers._elicit_provnote import (  # noqa: F401
    _provenance_gen_note_detail,
    unrecognized_client_note,
)
from handlers._elicit_round import (  # noqa: F401
    _elicit_text,
    elicit_choice,
)
from tools.device_manager import list_devices
from tools.suite_store import list_recent_suites

# 2026-08-10 (K1): how long ANY single MCP elicitation may hold a tool call open.
# Named for the export dialog until this batch, because that was the only bounded
# site; every _elicit_text/elicit_choice site is bounded now, so the export-only
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
# WHY 20.0 and not an arbitrary smaller number: 20.0 IS ELICIT_FLOOR_S below. The
# floor already asks a call's FIRST dialog for at least 20s even on a spent budget,
# so any value under 20 would make this constant a bound the floor routinely
# overrides -- a number that does not describe the behaviour, which is the class of
# untrue text this whole area exists to remove -- and would break the pinned
# invariant ELICIT_FLOOR_S <= ELICIT_TIMEOUT_S. At 20.0 the three-dialog wizard's
# worst case is 3x20 = 60s inside the unchanged 80s per-call budget, still well
# under ~120s, and one blind dialog costs a tester 20s instead of 55s. A shorter
# bound also makes LATE orphan answers more frequent, which is exactly what
# _suppress_orphan_elicit_logs handles.
ELICIT_TIMEOUT_S = 20.0


# Per-CALL budget. Dialogs chain sequentially inside one tool call (the image gate
# asks twice, the wizard up to three times), so a per-dialog bound alone still let
# one call run 110-220s and die at the client's idle timeout. 80.0 + one
# ELICIT_FLOOR_S is 100s worst case from tool entry -- under ~120s with margin.
_ELICIT_CALL_BUDGET_S = 80.0

# The FIRST dialog of a call is always asked, for at least this long, even when the
# budget is already spent. Without it a dialog sitting behind minutes of work --
# auto_export_xlsx runs at the END of a full 8-category generation -- would be
# skipped silently, and on a legacy install the tester's answer is the only signal
# there is. Bounded: max(min(ELICIT_TIMEOUT_S, remaining), ELICIT_FLOOR_S) never
# exceeds ELICIT_TIMEOUT_S.
ELICIT_FLOOR_S = 20.0


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


# Public seams for the bounds above: readers call these at call time, tests patch
# the accessor (not the private constant).
def elicit_timeout_s() -> float:
    return ELICIT_TIMEOUT_S


def elicit_floor_s() -> float:
    return ELICIT_FLOOR_S


def instant_decline_s() -> float:
    return _INSTANT_DECLINE_S


def _dispatch_provenance() -> dict:
    """Call-time hop to ``dispatch_provenance`` (foreign patch seam)."""
    return dispatch_provenance()


# Offer the most recent stored suites as a choice (non-technical parity with
# Chainlit's recent-suites list). CHOSEN carries the suite_id.
async def elicit_suite(choose: _common.ChooseCb) -> _common.ChoiceResult:
    result = await list_recent_suites(limit=5)
    rows = result.get("content") or []
    if not rows:
        return _common.ChoiceResult(_common.UNAVAILABLE)
    labels, by_label = _suite_options(rows)
    picked = await elicit_choice(choose, "Which suite?", labels)
    if picked.status == _common.CHOSEN:
        return _common.ChoiceResult(_common.CHOSEN, by_label.get(picked.value or ""))
    return picked


async def recent_suites_markdown(tool: str) -> str:
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


async def _elicit_device(choose: _common.ChooseCb) -> _common.ChoiceResult:
    """Scan live devices and elicit one by name. Returns CHOSEN with the device
    id, or UNAVAILABLE (no callback / no devices / transport error)."""
    result = await list_devices()
    devices = result.get("content") or []
    if not devices:
        return _common.ChoiceResult(_common.UNAVAILABLE)
    labels, by_label = _device_options(devices)
    picked = await elicit_choice(choose, "Which device?", labels)
    if picked.status == _common.CHOSEN:
        return _common.ChoiceResult(_common.CHOSEN, by_label.get(picked.value or ""))
    return picked


_elicit_host.bind(sys.modules[__name__])
