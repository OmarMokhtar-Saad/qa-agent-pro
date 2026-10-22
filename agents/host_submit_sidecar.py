"""Review-sidecar stage of qa_submit_suite (moved from handlers, C3a step 1).

A sidecar carries duplicate_groups / acceptance_criteria / ambiguity_result
(plus checklist and image fields when the prep asked for them). A full
submission makes ``review_sidecar`` return None, so the retained-sidecar
overlay runs ONLY on the sidecar or bare-finalize route; the size bound
measures the bytes of THIS call, before any retained content is folded in.
Retention stores only the keys the sidecar mentioned, so present-but-empty
survives the round trip; ``update_prep`` supplies the byte and TTL bounds.
"""

from __future__ import annotations

import logging
from typing import Any, NamedTuple

from agents.host_prep_sidecar import (
    remap_dup_groups,
    review_sidecar,
)
from agents.host_prep_sidecar_keys import (
    GATE_ANSWER_KEYS,
    SIDECAR_RETAINABLE,
    retained_sidecar,
    sidecar_keys,
)
from agents.host_submit_ports_prep import SubmitPorts
from agents.host_submit_types import SubReq, SubRoute
from config.settings_guard import never_raise
from tools import prep_store

logger = logging.getLogger("agents.host_mode")


class MergeIn(NamedTuple):
    rows: list
    route: Any
    meta: dict


def route(req: SubReq, ports: SubmitPorts) -> str | SubRoute:
    meta = req.meta
    sidecar_obj = review_sidecar(req.suite_json, meta) if req.has_full else None
    superseded = ports.prep_load.superseded_sidecar_note(sidecar_obj, meta)
    oversized = ports.category.oversized_submission_note(sidecar_obj)
    if oversized:
        return oversized
    if sidecar_obj is not None or not req.has_full:
        sidecar_obj = ports.prep_load.merge_retained_sidecar(sidecar_obj, meta)
    raw = sidecar_obj.get("duplicate_groups") if isinstance(sidecar_obj, dict) else None
    return SubRoute(sidecar_obj, raw, superseded)


def _mentioned(sidecar_obj: Any) -> dict:
    return {
        k: sidecar_obj[k]
        for k in SIDECAR_RETAINABLE
        if isinstance(sidecar_obj, dict) and k in sidecar_obj
    }


async def _update(req: SubReq, mentioned: dict) -> Any:
    pending = {**retained_sidecar(req.meta), **mentioned}
    meta = {**req.meta, "pending_sidecar": pending}
    return await prep_store.update_prep(req.prep_id, {**req.envelope, "meta": meta})


# F3: RETAIN a sidecar that arrived before any category was staged.
async def try_retain(req: SubReq, sidecar_obj: Any) -> bool:
    mentioned = _mentioned(sidecar_obj)
    kept = False
    if mentioned:
        # pragma: no cover - defensive
        with never_raise(logger, "sidecar retain failed", exc_info=True):
            store = await _update(req, mentioned)
            kept = not (store or {}).get("error")
            if not kept:
                logger.warning(
                    "prep %s sidecar NOT retained (%s)",
                    req.prep_id,
                    (store or {}).get("error"),
                )
    return kept


def keys_label(meta: dict) -> str:
    keys = [k for k in sidecar_keys(meta) if k not in GATE_ANSWER_KEYS]
    return "`" + "` / `".join(keys) + "`"


def _retained_reply(prep_id: str, label: str) -> str:
    return (
        "ℹ️ Received a review sidecar "
        f"({label}) but no per-category rows are "
        f"staged for prep_id `{prep_id}` yet. **It was "
        "RETAINED, not discarded** -- record your categories "
        "with `qa_submit_category`, then finalize with "
        f"`qa_submit_suite(prep_id='{prep_id}')` and this "
        "review will be applied. You do not need to send it "
        "again."
    )


def _not_retained_reply(prep_id: str, label: str) -> str:
    return (
        "⚠️ Nothing to finalize: received a review sidecar "
        f"({label}) but no per-category rows are staged "
        f"for prep_id `{prep_id}`, and it could NOT be retained "
        "on the prep. Record your categories with "
        "`qa_submit_category` and send this sidecar again with "
        "the finalize call."
    )


# Say so plainly: a success-shaped reply for a review that was NOT kept is
# the failure this reply exists to remove.
async def no_rows_reply(req: SubReq, sidecar_obj: Any, ports: SubmitPorts) -> str:
    label = keys_label(req.meta)
    kept = await ports.io.try_retain(req, sidecar_obj)
    if kept:
        return _retained_reply(req.prep_id, label)
    return _not_retained_reply(req.prep_id, label)


# Copy each key only when the sidecar carried it (present-but-empty reads as
# "reviewed and found none"). No id remap for checklist or image entries: they
# are keyed by position or server-assigned id, never by tc_id.
def _overlay(merged: dict, sidecar_obj: Any, meta: dict) -> None:
    for key in ("acceptance_criteria", "ambiguity_result") + GATE_ANSWER_KEYS:
        if sidecar_obj.get(key) is not None:
            merged[key] = sidecar_obj.get(key)
    if meta.get("host_checklist_job"):
        if sidecar_obj.get("checklist_items") is not None:
            merged["checklist_items"] = sidecar_obj.get("checklist_items")
    if meta.get("host_image_job"):
        if sidecar_obj.get("image_descriptions") is not None:
            merged["image_descriptions"] = sidecar_obj.get("image_descriptions")


# F7: NO size cap on the SERVER's own merge; every staged row was already
# measured at qa_submit_category time.
def merge(args: MergeIn, ports: SubmitPorts) -> tuple[Any, int]:
    sidecar_raw = args.route.sidecar_raw
    merged_dict, used, id_map = ports.prep_load.merge_category_rows(args.rows)
    merged_dict = dict(merged_dict)
    if sidecar_raw is not None:
        merged_dict["duplicate_groups"] = remap_dup_groups(sidecar_raw, id_map)
    _overlay(merged_dict, args.route.sidecar_obj, args.meta)
    parsed = ports.io.parse_host_suite(merged_dict, enforce_size_cap=False)
    return parsed, used
