"""prep_status leaf: the category-job packet replies (qa_get_category_job)."""

from __future__ import annotations

import logging
from typing import Any

from agents.host_prep_reply_text import (
    all_jobs_text,
    no_jobs_text,
    single_job_text,
    unknown_category_text,
    with_step_zero,
)
from agents.host_prep_serde import PrepSerdeError
from agents.host_submission import safe_prep_id
from handlers import _common
from handlers.prep_status_host import host
from tools import prep_store

logger = logging.getLogger("tools.mcp_handlers")


def _all_jobs_reply(envelope: Any, prepared: Any, prep_id: str) -> str:
    """The "all" form: every job packet in one fenced JSON, step zero first."""
    batch = host().build_category_jobs_batch(prepared, prep_id)
    if batch is None:
        return no_jobs_text(prep_id)
    return all_jobs_text(with_step_zero(batch, envelope), prep_id)


def _single_job_reply(prepared: Any, prep_id: str, name: str) -> str:
    """The one-category form: that job packet as fenced JSON, or a refusal."""
    job = host().build_category_job(prepared, prep_id, name)
    if job is None:
        return unknown_category_text(prep_id, name)
    return single_job_text(job.get("category_name"), prep_id, job)


async def _category_job_reply(prep_id: str, category_name: str) -> str:
    # Fix 5b: without the finalized refusal, a finalized prep would be served a
    # FRESH worker packet and touch_prep would slide its TTL -- re-arming the
    # regenerate-after-finish loop.
    h = host()
    envelope, refusal = await h.load_open_prep(prep_id)
    if refusal:
        return refusal
    prepared = h.deserialize_prepared(envelope.get("prepared") or {})
    # 2026-07-31 incident: fetching a worker packet is real orchestration activity.
    # prep_store gates the write (no-op unless the sliding TTL or the unfinished
    # disclosure is on) and never raises, so the fetch is never blocked by it.
    await prep_store.touch_prep(prep_id)
    if category_name.lower() in ("all", "*"):
        return _all_jobs_reply(envelope, prepared, prep_id)
    return _single_job_reply(prepared, prep_id, category_name)


async def handle_get_category_job(prep_id: str, category_name: str) -> str:
    """Return one self-contained category job packet as fenced JSON. Never raises."""
    # UNTRUSTED at every entry point: the id is host-supplied and echoed back, so
    # it is gated HERE, once, rather than at every downstream interpolation.
    prep_id = safe_prep_id(prep_id)
    category_name = (category_name or "").strip()
    if not prep_id:
        return "⚠️ Missing prep_id."
    if not category_name:
        return "⚠️ Missing category_name."
    try:
        return await _category_job_reply(prep_id, category_name)
    except PrepSerdeError as exc:
        return f"⚠️ Could not read this prep: {exc}"
    except Exception as exc:
        logger.exception("handle_get_category_job failed")
        _common.capture_error(exc, "qa_get_category_job")
        return f"⚠️ Could not build category job: {exc}"
