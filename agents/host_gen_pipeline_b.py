"""Host mode: stage the prepared envelope and compose the prepare reply (C3c).

Moved from handlers/generate.py. Every call that reaches a handler module, or a
name tests patch on ``handlers.generate``, goes through the hook groups.
"""

from __future__ import annotations

import logging

from agents.host_gen_args import FinishCtx
from agents.host_gen_hooks import CaptureHooks
from agents.host_gen_types import PreparePayloadResult
from config.settings_guard import never_raise
from tools import prep_store

logger = logging.getLogger("agents.host_mode")


async def save_prep_or_refuse(
    envelope: dict, capture_ids: list | None, capture: CaptureHooks
) -> str | PreparePayloadResult:
    """Save the envelope: the new prep_id, or the "Could not stage" clarify."""
    saved = await prep_store.save_prep(envelope, created_by="qa_prepare_test_cases")
    prep_id = (saved.get("content") or {}).get("prep_id") or ""
    if saved.get("error") or not prep_id:
        return PreparePayloadResult(
            clarify=(
                "⚠️ Could not stage the prepared generation: "
                f"{saved.get('error') or 'unknown store error'}"
                + capture.retry_hint(capture_ids)
            )
        )
    return prep_id


# F3 (2026-08-30): the step-zero jobs live on the PAYLOAD, which is
# built AFTER save_prep and never persisted -- so
# `qa_get_category_job(prep_id, "all")`, the one call this server's own
# `instructions` block tells a host to make, returned the 8 category
# jobs and NONE of the step-zero ones, including the ambiguity preflight
# marked `blocking: true` and the acceptance-criteria job whose ac_ids
# every case's requirement_id must cite. A host following the
# instructions literally therefore discovered the AC job at FINALIZE,
# by which time all 95 cases were staged with requirement_id null and
# the only remedy was regenerating the suite (measured 2026-08-30: 0 of
# 95 traced following the instructions, 79 of 79 running step 0 first).
# Persist the step-zero subset so that call can serve it. Best-effort:
# a failed update costs the passthrough, never the prep.
async def persist_step_zero(payload: dict, envelope: dict, prep_id: str) -> None:
    """Best-effort: persist the payload's step-zero jobs onto the saved envelope."""
    # pragma: no cover - never costs the prepare
    with never_raise(logger, "step-zero passthrough persist failed", exc_info=True):
        _sz_index = [
            _e
            for _e in (payload.get("jobs_to_run") or [])
            if isinstance(_e, dict) and str(_e.get("stage") or "") == "step_zero"
        ]
        if _sz_index:
            _sz: dict = {"jobs_to_run": _sz_index}
            for _e in _sz_index:
                _k = str(_e.get("payload_key") or "")
                if _k and isinstance(payload.get(_k), dict):
                    _sz[_k] = payload[_k]
            envelope["step_zero"] = _sz
            await prep_store.update_prep(prep_id, envelope)


def _server_notice(ctx: FinishCtx) -> str:
    run = ctx.run
    return ctx.hooks.notes.server_llm_notice(
        ac_boomeranged=run.ac_job,
        img_boomeranged=run.img.job,
        checklist_boomeranged=run.checklist_job,
        rule_packs_narrowed=run.rp_narrowed,
    )


def _image_parts(ctx: FinishCtx, url_content: dict) -> tuple:
    run, hooks = ctx.run, ctx.hooks
    screen_ref = str(url_content.get("image_reference_advisory") or "")
    return (
        hooks.notes.ticket_image_note(
            url_content, run.intake.attested, run.captured_shipped
        ),
        hooks.notes.captured_screens_note(run.cap_labels, run.img.job),
        hooks.image.grounding_source_note(
            run.text, url_content, run.grounded.openapi_text
        ),
        ("> ℹ️ " + screen_ref) if screen_ref else "",
    )


def _recency_parts(ctx: FinishCtx) -> tuple:
    run = ctx.run
    return (
        ctx.hooks.notes.snapshot_note(run.notes.snapshot_updated),
        run.notes.stale,
        run.notes.recent,
        run.notes.condensed,
        run.carry.carry_note,
        ctx.missing_note(),
    )


def _reply_parts(ctx: FinishCtx) -> tuple:
    url_content = ctx.run.grounded.url_content or {}
    return (*_image_parts(ctx, url_content), *_recency_parts(ctx))


# The server-call note first, then every disclosure (ticket images, captured
# screens, grounding source, screen reference, snapshot, stale, recent,
# condensed, revive / carry-forward, capture ids with no screen), then the
# unfinished-preps note.
async def prepare_reply_notice(ctx: FinishCtx, prep_id: str) -> str:
    """The prepare reply's notice: server-call note, then every disclosure."""
    hooks = ctx.hooks
    notice = _server_notice(ctx)
    notice = hooks.notes.reply_notes(notice, *_reply_parts(ctx))
    unfinished = await hooks.run.unfinished_note(exclude_prep_id=prep_id)
    return hooks.notes.reply_notes(notice, unfinished)
