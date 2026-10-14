"""prep_status leaf: the staged-vs-expected report (qa_prep_status)."""

from __future__ import annotations

from agents.host_orchestration import expected_category_names
from agents.host_submission import safe_prep_id
from config.settings_guard import never_raise
from handlers._boundary import handler_guard
from handlers.prep_status_host import host
from tools import prep_store
from tools.error_boundary import text_reply


async def handle_prep_status(prep_id: str) -> str:
    """Report staged vs expected categories for a prep_id. Never raises."""
    # UNTRUSTED at every one of these entry points: the id is whatever the
    # host sent, and it is echoed back in refusals and next-step
    # instructions, so an unsanitised one can close its code span and write
    # markdown into text the model then follows. Gated HERE, once, rather
    # than at the eighteen interpolations downstream.
    prep_id = safe_prep_id(prep_id)
    if not prep_id:
        return "⚠️ Missing prep_id."
    return await _prep_status_body(prep_id)


async def _staged_names(prep_id: str) -> tuple[list, str]:
    """Staged category names, or ``([], reply)`` when the submissions read failed."""
    rows_res = await prep_store.load_submissions(prep_id)
    # `content` alone cannot tell 'nothing staged' from 'the store could not be
    # read'; a store failure as `staged: 0/8` invites a re-stage of all eight.
    note = host().prep_store_error_reply(prep_id, rows_res)
    if note:
        return [], note
    rows = rows_res.get("content") or []
    return [str(r.get("category_name") or "") for r in rows if isinstance(r, dict)], ""


@handler_guard(
    "qa_prep_status",
    "handle_prep_status failed",
    text_reply("⚠️ Could not read prep status: "),
)
async def _prep_status_body(prep_id: str) -> str:
    # Fix 5b: a finalized prep now LOADS, so every reader checks the stamp before
    # rehydrating (else `staged: 0/8` plus a Path A re-stage of a finished suite).
    # B1 (2026-08-21): wrapped in the status shape so finalized is a TERMINAL state.
    h = host()
    envelope, refusal = await h.load_open_prep(
        prep_id, wrap_final=h._prep_status_finalized_reply
    )
    if refusal:
        return refusal
    expected = _prep_expected_names(envelope)
    names, note = await _staged_names(prep_id)
    if note:
        return note
    status = h.prep_status_view(expected=expected, staged_raw_names=names)
    return h._prep_status_reply(prep_id, status)


def _prep_expected_names(envelope: dict) -> list:
    """Expected category names for a prep: recorded, else derived from `prepared`."""
    meta = envelope.get("meta") or {}
    expected = list(meta.get("expected_categories") or [])
    if not expected and not meta.get("parallel_fanout"):
        with never_raise() as g:
            prepared = host().deserialize_prepared(envelope.get("prepared") or {})
            expected = expected_category_names(prepared)
        if g.failed:
            expected = []
    return expected
