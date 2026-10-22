"""Writing the case checkpoint: the body, what it carries forward, the payload."""

from __future__ import annotations

from typing import Any

from tools.mobile import case_run_start as start_mod
from tools.mobile import permission_note, run_store
from tools.mobile.case_run_counts import _prior_uncharged
from tools.mobile.case_run_evidence import _prior_content
from tools.mobile.case_run_notrun import attach_not_run, queued_before
from tools.mobile.case_run_record import _evidence_record
from tools.mobile.case_run_trace import merge_traces
from tools.mobile.case_run_types import _Case, _CpFields, _Step


# The checkpoint fields that are carried, cleared or taken from the call.
#
# ``uncharged_stops``: ONE carry-forward for both reasons. Every terminal
# checkpoint replaces this body whole, so a count this call did not compute
# must survive -- and `_prior_uncharged` guards the read on BOTH sides,
# because a corrupt count on disk (a hand-edited checkpoint, a file from
# another build) used to raise inside every later checkpoint for that
# case, which `submit_case`'s own except then reported as a handled
# error with no verdict written.
#
# ``guard_stop``: NOT carried forward from `prior`, unlike `uncharged_stops`:
# a guard stop this call did not just produce is not THIS turn's stop, and
# carrying an old one forward would let a later turn's confirm match a
# control nobody stopped on THIS submission.
#
# ``queued_actions``: CLEARED BY DEFAULT (`queued_actions=None`), unlike
# `uncharged_stops`: a queue not explicitly carried forward is queue the model
# has either consumed or that no longer applies -- see call sites for the
# three cases that pass it explicitly.
#
# ``media``: CARRY-FORWARD, same reason as `started` and `evidence`: this body
# REPLACES the case whole, and `media.remember` wrote its records onto the
# case moments ago, on this same submit. Without this line the clips and
# frames are written to disk and then erased from their only index before any
# consumer sees one -- silently, because the report simply finds no media and
# renders the old wireframe.
def _carried_fields(prior: dict, fields: _CpFields) -> dict:
    """The checkpoint fields that are carried, cleared or taken from the call."""
    uncharged, guard_stop = fields.uncharged, fields.guard_stop
    queued = fields.queued_actions
    return {
        "uncharged_stops": (
            dict(uncharged) if isinstance(uncharged, dict) else _prior_uncharged(prior)
        ),
        "guard_stop": dict(guard_stop) if isinstance(guard_stop, dict) else {},
        "queued_actions": list(queued) if isinstance(queued, list) else [],
        "media": [
            item for item in list(prior.get("media") or []) if isinstance(item, dict)
        ],
    }


# Carried FORWARD from the planning checkpoint, which this body replaces
# whole: the case's start time and its evidence record (plan D6). Without
# this, ``started`` survived only until the first submit -- and the join
# window needs both ends.
def _checkpoint_body(case: _Case, fields: _CpFields, prior: dict) -> dict:
    """The document a checkpoint stores: this call's fields over the carried ones."""
    now = start_mod._now()
    return {
        "tc_id": case.tc_id,
        "title": case.view.get("title") or "",
        "verdict": fields.verdict,
        "status": fields.status,
        "reason": str(fields.reason or "")[:1200],
        # ACCUMULATED, not replaced -- see `merge_traces`.
        "trace": merge_traces(prior.get("trace"), fields.trace),
        "escapes": int(fields.escapes),
        "started": prior.get("started") or now,
        "updated": now,
        "evidence": _evidence_record(prior.get("evidence")),
        **_carried_fields(prior, fields),
    }


def _write_checkpoint(case: _Case, fields: _CpFields) -> dict:
    """Store the checkpoint body and return the caller's payload."""
    prior = _prior_content(case.run_id, case.tc_id)
    body = _checkpoint_body(case, fields, prior)
    written = run_store.write_case(case.run_id, case.tc_id, body)
    payload = dict(body)
    payload["packet"] = fields.packet
    payload["checkpoint_error"] = written.get("error")
    return payload


# Write the case checkpoint and return the caller's payload.
#
# The trace entries were already redacted by ``executor``; ``run_store``
# redacts again on the way to disk. Two layers on purpose: this payload is
# ALSO returned to the caller and rendered, and only one of those two paths
# goes through the store.
def _checkpoint(run_id: str, tc_id: str, view: dict, **fields: Any) -> dict:
    """Write the case checkpoint and return the caller's payload."""
    return _write_checkpoint(_Case(run_id, tc_id, view), _CpFields(**fields))


def _step_payload(step: _Step, view: dict, **fields: Any) -> dict:
    """``{"error": None, "content": <checkpoint>}`` for *step*.

    ``escapes`` defaults to the step's own count; a branch that charged one
    passes the new total.
    """
    fields.setdefault("escapes", step.used)
    queued_n = queued_before(step.run_id, step.tc_id)
    content = _checkpoint(step.run_id, step.tc_id, view, trace=step.trace, **fields)
    payload = {"error": None, "content": content}
    if content and content.get("status") == "needs_model":
        permission_note.annotate(content, step.new_screen)
    return attach_not_run(payload, step.result, queued_n)
