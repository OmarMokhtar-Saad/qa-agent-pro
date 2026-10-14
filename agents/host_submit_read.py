"""Read stage of qa_submit_suite: turn the call into a parsed suite (C3a step 1).

CONFLICT RULE: a non-empty ``suite_json`` is AUTHORITATIVE and accumulated
per-category rows are ignored (the reply says how many were not used); an
empty one merges the accumulated rows. An empty object, or one whose only
content is an empty ``test_cases``, means exactly what "" means. A review
sidecar is NOT empty, so it still takes ``has_full`` and reaches the
sidecar route. The tester's own bytes are measured before any retained
content is folded in, and the merged result is parsed EXEMPT from the cap.
"""

from __future__ import annotations

from typing import Any

from agents.host_prep_serde import PrepSerdeError
from agents.host_submit_ports_prep import SubmitPorts
from agents.host_submit_sidecar import keys_label, no_rows_reply, route
from agents.host_submit_types import SubRead, SubReq, SubRoute
from tools import prep_store


def has_full_submission(suite_json: Any) -> bool:
    if isinstance(suite_json, str):
        has_full = bool(suite_json.strip())
    elif suite_json is None:
        has_full = False
    elif isinstance(suite_json, dict):
        if not suite_json:
            has_full = False
        elif set(suite_json) <= {"test_cases"} and not suite_json.get("test_cases"):
            has_full = False
        else:
            has_full = True
    else:
        has_full = True
    return has_full


async def _load_rows(req: SubReq, ports: SubmitPorts) -> tuple[str, list]:
    rows_res = await prep_store.load_submissions(req.prep_id)
    note = ports.prep_load.prep_store_error_reply(req.prep_id, rows_res)
    if note:
        return note, []
    return note, rows_res.get("content") or []


def _sidecar_note(route_: SubRoute, used: int, meta: dict) -> str:
    return route_.superseded_note + (
        f"> ℹ️  Finalized from {used} accumulated per-category "
        f"row(s) plus a review sidecar ({keys_label(meta)}) (no full "
        "suite_json test_cases were submitted).\n\n"
    )


# The SIDECAR finalize merges the same staged rows and deletes the prep on
# success, so it needs the same completeness gate as the bare finalize.
async def via_sidecar(
    req: SubReq, route_: SubRoute, ports: SubmitPorts
) -> str | SubRead:
    note, rows = await _load_rows(req, ports)
    if note:
        return note
    if not rows:
        return await no_rows_reply(req, route_.sidecar_obj, ports)
    gate = ports.prep_load.fanout_incomplete_note(req.meta, rows, req.prep_id)
    if gate:
        return gate
    parsed, used = ports.io.sidecar_merge(rows, route_, req.meta)
    return SubRead(parsed, False, _sidecar_note(route_, used, req.meta), rows)


async def full_suite(req: SubReq, ports: SubmitPorts) -> str | SubRead:
    conflict_note = ""
    oversized = ports.reply.oversized_full_suite_note(req.suite_json)
    if oversized:
        return oversized
    parsed = ports.io.parse_host_suite(
        ports.prep_gate.with_retained_sidecar(req.suite_json, req.meta),
        enforce_size_cap=False,
    )
    rows_res = await prep_store.load_submissions(req.prep_id)
    rows = rows_res.get("content") or []
    if rows:
        conflict_note = (
            f"> ℹ️  A full suite was submitted, so {len(rows)} "
            "accumulated per-category row(s) were NOT used.\n\n"
        )
    return SubRead(parsed, True, conflict_note, rows)


def _nothing_staged_reply(prep_id: str) -> str:
    return (
        "⚠️ Nothing to finalize: no suite_json was provided "
        "and no per-category rows are staged for prep_id "
        f"`{prep_id}`. Submit the merged JSON, or record categories "
        "first with `qa_submit_category`."
    )


# A store failure would otherwise read as 'no rows are staged'. Path A
# completeness gate: refuse a partial staged set when the prep asked for
# parallel fan-out; shared with the sidecar branch.
async def staged_rows(req: SubReq, ports: SubmitPorts) -> str | SubRead:
    note, rows = await _load_rows(req, ports)
    if note:
        return note
    if not rows:
        return _nothing_staged_reply(req.prep_id)
    gate = ports.prep_load.fanout_incomplete_note(req.meta, rows, req.prep_id)
    if gate:
        return gate
    merged_dict, used, _id_map = ports.prep_load.merge_category_rows(rows)
    parsed = ports.io.parse_host_suite(merged_dict, enforce_size_cap=False)
    conflict_note = (
        f"> ℹ️  Finalized from {used} accumulated per-category "
        "row(s) (no full suite_json was submitted).\n\n"
    )
    return SubRead(parsed, False, conflict_note, rows)


# "Fix the JSON" is right for a parse failure and WRONG for a validation
# failure, where every case was rejected on a FIELD rule.
def _serde_reply(exc: PrepSerdeError, prep_id: str) -> str:
    _fix = (
        "Fix the fields named above on each case and resubmit"
        if "failed validation" in str(exc)
        else "Fix the JSON and resubmit"
    )
    return (
        f"⚠️ Could not read the submitted suite: {exc}\n\n"
        f"{_fix} with the same prep_id `{prep_id}`."
    )


async def read_submission(req: SubReq, ports: SubmitPorts) -> str | SubRead:
    try:
        route_ = route(req, ports)
        if isinstance(route_, str):
            return route_
        if route_.sidecar_obj is not None:
            return await via_sidecar(req, route_, ports)
        elif req.has_full:
            return await full_suite(req, ports)
        else:
            return await staged_rows(req, ports)
    except PrepSerdeError as exc:
        return _serde_reply(exc, req.prep_id)
