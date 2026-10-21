"""Host mode: stage, build the payload, audit and ship the prepare reply (C3c).

Moved from handlers/generate.py. Calls into handler modules and patched names go
through the hook groups carried by FinishCtx.
"""

from __future__ import annotations

from agents.host_gen_args import FinishCtx
from agents.host_gen_hooks import GenerateHooks
from agents.host_gen_meta_a import meta_head
from agents.host_gen_meta_b import build_prepare_payload, meta_stamps
from agents.host_gen_pipeline_b import (
    persist_step_zero,
    prepare_reply_notice,
    save_prep_or_refuse,
)
from agents.host_gen_types import PreparePayloadResult, _PrepRun


def _envelope(run: _PrepRun, hooks: GenerateHooks) -> dict:
    serialized = hooks.run.serialize_prepared(run.prepared)
    return {
        "prepared": serialized,
        "meta": {**meta_head(run, hooks), **meta_stamps(run, hooks.run)},
    }


def _prepare_audit_detail(run: _PrepRun) -> dict:
    return {
        "host_ambiguity_review": bool(run.host_amb),
        "host_ac_job": bool(run.ac_job),
        "host_image_job": bool(run.img.job),
        "host_image_relevance": bool(run.img.relevance),
        "host_image_preflight": bool(run.img.preflight),
        "host_image_require_relevant": bool(run.img.require),
        "captured_image_count": run.captured_shipped,
        "captured_image_read": run.intake.captured,
        "carried_forward_capture_count": len(run.carry.carried_ids),
        "image_carry_ack": bool(run.image_carry_ack),
        "host_checklist_job": bool(run.checklist_job),
    }


async def _audit_prepare(ctx: FinishCtx, prep_id: str) -> None:
    await ctx.hooks.common.audit(
        "mcp_prepare_test_cases",
        entity_id=prep_id,
        detail=_prepare_audit_detail(ctx.run),
    )


async def _stage(ctx: FinishCtx) -> tuple:
    """Serialize, save, build the payload: (prep_id or refusal, payload)."""
    run, hooks = ctx.run, ctx.hooks
    envelope = _envelope(run, hooks)
    prep_id = await save_prep_or_refuse(envelope, run.capture_ids, hooks.capture)
    if isinstance(prep_id, PreparePayloadResult):
        return prep_id, {}
    payload = build_prepare_payload(run, prep_id, hooks.jobs)
    await persist_step_zero(payload, envelope, prep_id)
    return prep_id, payload


# Item 6: carry the RAW ticket screenshots so the TOOL layer can forward them to
# the host's OWN multimodal model (host-supplied only; this server sends no images
# and makes no vision call at all). Present only when JIRA_FETCH_IMAGES + ANTHROPIC_API_KEY
# let jira_fetcher download them; bytes are never persisted in the prep store.
# QA_HOST_IMAGE_DESCRIPTION_ENABLED additionally forwards the tester's chat
# attachments; OFF, this is EXACTLY today's ticket-images-only list.
async def finish_prepare(ctx: FinishCtx) -> PreparePayloadResult:
    """Stage the envelope, build the payload, audit, and ship the reply."""
    prep_id, payload = await _stage(ctx)
    if isinstance(prep_id, PreparePayloadResult):
        return prep_id
    await _audit_prepare(ctx, prep_id)
    ticket_images = list(ctx.run.prospective_images)
    notice = await prepare_reply_notice(ctx, prep_id)
    # The screens have ACTUALLY shipped now, so the tray entries can go --
    # and not one line earlier. Popping them where they were read killed
    # every capture on a Jira source, because that round returns the fetch
    # DIRECTIVE and prepares nothing.
    ctx.hooks.capture.drop_captures(ctx.run.capture_ids)
    return PreparePayloadResult(
        payload=payload, prep_id=prep_id, images=ticket_images, notice=notice
    )
