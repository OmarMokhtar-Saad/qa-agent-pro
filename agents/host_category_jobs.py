"""Host mode: per-category jobs, category normalisation and notes. Cut verbatim from agents/host_mode.py (P4.3).

The leaves are agents/host_category_*.py; this module keeps the two builders that tests patch
and re-exports every name the callers import."""

from __future__ import annotations

import logging

from agents.host_category_consts import _EXAMPLE_VALID_CASE, logger  # noqa: F401
from agents.host_category_names import (  # noqa: F401
    _canonical_categories,
    normalize_category,
)
from agents.host_category_notes import (  # noqa: F401
    category_checklist_note,
    category_dedup_note,
)
from agents.host_category_packet import _collect_batch, _job_packet
from agents.host_category_parts import _category_prompt_parts
from agents.host_category_status import prep_status_view  # noqa: F401
from agents.host_category_text import _thin_source  # noqa: F401
from config.settings_guard import never_raise


# Self-contained packet for qa_get_category_job. None if unknown/unusable.
#
# Includes system_prompt + user_context + instruction + response_schema for ONE
# category. category_name is resolved via normalize_category. Never raises.
#
# Implementation note: rebuilds the shared prompt pieces the same way
# build_prepare_payload does (do NOT call build_prepare_payload from here in a
# way that re-enters job construction -- call the shared helpers / duplicate the
# small assembly). Prefer assembling from _category_shared_system + the matching
# categories[] row from a local loop identical to build_prepare_payload.
def build_category_job(prepared, prep_id: str, category_name: str) -> dict | None:
    """Self-contained packet for qa_get_category_job. None if unknown/unusable."""
    with never_raise(
        logger, "build_category_job failed", level=logging.WARNING, exc_info=True
    ):
        canon = normalize_category(category_name) or str(category_name or "").strip()
        if not canon:
            return None
        parts = _category_prompt_parts(prepared, canon)
        if parts is None:
            return None
        return _job_packet(prepared, prep_id, parts)
    return None


# EVERY category job in ONE packet, shared fields hoisted once.
#
# 2026-08-04: the 22:11 Cursor run made 8 sequential qa_get_category_job
# calls (22:19:58-22:20:14) after a 2-minute re-read of the prepare blob.
# One fetch carries the same information with the big shared blocks
# (system_prompt, user_context, response_schema, worker_instructions)
# stated ONCE instead of 8 times: ``shared`` + one ``jobs[]`` entry is
# byte-equivalent to the single-category packet. Never raises; None when
# the prep carries no usable categories.
def build_category_jobs_batch(prepared, prep_id: str) -> dict | None:
    """EVERY category job in ONE packet, shared fields hoisted once. None when unusable."""
    with never_raise(
        logger, "build_category_jobs_batch failed", level=logging.WARNING, exc_info=True
    ):
        return _collect_batch(prepared, prep_id, build_category_job)
    return None
