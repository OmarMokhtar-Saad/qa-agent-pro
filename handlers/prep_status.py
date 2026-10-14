"""Moved from handlers (refactor P2): prep_status."""

from __future__ import annotations

import logging
import sys
from typing import Any  # noqa: F401

from agents.host_category_jobs import (
    build_category_job,  # noqa: F401
    build_category_jobs_batch,  # noqa: F401
    prep_status_view,  # noqa: F401
)
from agents.host_image import image_relevance_counts  # noqa: F401
from agents.host_orchestration import expected_category_names  # noqa: F401
from agents.host_prep_merge import (
    collect_category_cases as _collect_category_cases,  # noqa: F401
)
from agents.host_prep_merge import (
    fanout_incomplete_note as fanout_incomplete_note,  # noqa: F401
)
from agents.host_prep_merge import (
    merge_category_rows as merge_category_rows,  # noqa: F401
)
from agents.host_prep_notes import dropped_note as dropped_note  # noqa: F401
from agents.host_prep_notes import (  # noqa: F401
    no_coverage_signal_note as no_coverage_signal_note,
)
from agents.host_prep_open import unfinished_preps_note  # noqa: F401
from agents.host_prep_relevance_gate import (  # noqa: F401
    RelevanceCheck,
    image_relevance_gate,
)
from agents.host_prep_relevance_verdict import (
    _IMAGE_GATE_MAX_NAMED as _IMAGE_GATE_MAX_NAMED,
)
from agents.host_prep_remap import (
    remap_chained_from as _remap_chained_from,  # noqa: F401
)
from agents.host_prep_remap import (
    remap_chained_item as _remap_chained_item,  # noqa: F401
)
from agents.host_prep_reply_text import (
    all_jobs_text,  # noqa: F401
    finalized_text,  # noqa: F401
    host_task_text,  # noqa: F401
    missing_text,  # noqa: F401
    no_jobs_text,  # noqa: F401
    single_job_text,  # noqa: F401
    store_error_text,  # noqa: F401
    unknown_category_text,  # noqa: F401
    with_step_zero,  # noqa: F401
)
from agents.host_prep_rtm import _RTM_ORPHAN_ID_CAP as _RTM_ORPHAN_ID_CAP
from agents.host_prep_rtm import _RTM_ORPHAN_MAX_NAMED as _RTM_ORPHAN_MAX_NAMED
from agents.host_prep_rtm import _RTM_ORPHAN_TITLE_CAP as _RTM_ORPHAN_TITLE_CAP
from agents.host_prep_rtm import rtm_orphan_note as rtm_orphan_note  # noqa: F401
from agents.host_prep_rtm import rtm_trace_detail as rtm_trace_detail  # noqa: F401
from agents.host_prep_serde import PrepSerdeError, deserialize_prepared  # noqa: F401
from agents.host_prep_sidecar import (
    dedup_sidecar_groups as _dedup_sidecar_groups,  # noqa: F401
)
from agents.host_prep_sidecar import (
    merge_retained_sidecar as merge_retained_sidecar,  # noqa: F401
)
from agents.host_prep_sidecar import (
    remap_dup_groups as _remap_dup_groups,  # noqa: F401
)
from agents.host_prep_sidecar import review_sidecar as _review_sidecar  # noqa: F401
from agents.host_prep_sidecar import (
    superseded_sidecar_note as superseded_sidecar_note,  # noqa: F401
)
from agents.host_prep_sidecar import (
    with_retained_sidecar as with_retained_sidecar,  # noqa: F401
)
from agents.host_prep_sidecar_keys import _SIDECAR_KEYS as _SIDECAR_KEYS
from agents.host_prep_sidecar_keys import _SIDECAR_RETAINABLE as _SIDECAR_RETAINABLE
from agents.host_prep_sidecar_keys import (
    retained_sidecar as _retained_sidecar,  # noqa: F401
)
from agents.host_prep_sidecar_keys import sidecar_keys as _sidecar_keys  # noqa: F401
from agents.host_prep_skew import code_skew_note as code_skew_note  # noqa: F401
from agents.host_prep_skew import version_skew_note as version_skew_note  # noqa: F401
from agents.host_prep_status_text import (
    finalize_hint as _prep_status_finalize_hint,  # noqa: F401
)
from agents.host_prep_status_text import (
    finalized_reply as _prep_status_finalized_reply,  # noqa: F401
)
from agents.host_prep_status_text import (
    state_line as _prep_status_state_line,  # noqa: F401
)
from agents.host_prep_status_text import (
    status_reply as _prep_status_reply,  # noqa: F401
)
from agents.host_prep_volume import _VOLUME_MAX_NAMED as _VOLUME_MAX_NAMED
from agents.host_prep_volume import _VOLUME_REFUSE_RATIO as _VOLUME_REFUSE_RATIO
from agents.host_prep_volume import RoutePhrases, VolumeCheck, VolumeQuery  # noqa: F401
from agents.host_prep_volume_note import volume_floor_note  # noqa: F401
from agents.host_prep_volume_note import (
    volume_shortfall_detail as volume_shortfall_detail,  # noqa: F401
)
from agents.host_prep_volume_refusal import (
    volume_shown as _volume_shown,  # noqa: F401
)
from agents.host_submission import safe_prep_id  # noqa: F401
from handlers import _common, export  # noqa: F401
from handlers._boundary import handler_guard  # noqa: F401
from handlers.prep_status_gates import (
    prep_image_relevance_gate,  # noqa: F401
    prep_unfinished_note,  # noqa: F401
    prep_volume_floor_note,  # noqa: F401
)
from handlers.prep_status_host import bind
from handlers.prep_status_jobs import (
    _all_jobs_reply,  # noqa: F401
    _category_job_reply,  # noqa: F401
    _single_job_reply,  # noqa: F401
    handle_get_category_job,  # noqa: F401
)
from handlers.prep_status_load import (
    _finalized_reply,  # noqa: F401
    _host_task_reply,  # noqa: F401
    _open_refusal,  # noqa: F401
    _prep_missing_reply,  # noqa: F401
    load_open_prep,  # noqa: F401
    prep_store_error_reply,  # noqa: F401
)
from handlers.prep_status_status import (
    _prep_expected_names,  # noqa: F401
    _prep_status_body,  # noqa: F401
    _staged_names,  # noqa: F401
    handle_prep_status,  # noqa: F401
)
from tools import host_llm, prep_store  # noqa: F401
from tools.error_boundary import text_reply  # noqa: F401

logger = logging.getLogger("tools.mcp_handlers")

# --------------------------------------------------------------------------- #
# Host-mode ("boomerang") SUBMIT handlers -- ops-3d-1b
#
# The BACK half of host-mode generation. handle_submit_suite validates the
# host-generated suite, runs the SHARED finalize_generation, and returns EITHER
# a one-round gap report (regenerate + resubmit with the same prep_id) OR the
# finished, persisted, exported suite. handle_submit_category records one
# category at a time for a weaker host. Like the PREPARE half this is DEAD CODE
# behind QA_GENERATION_MODE until ops-3d-3 wires routing -- nothing here is
# reachable from a server-mode path, and host-submitted JSON is treated as
# UNTRUSTED throughout (json.loads only, size-capped, and sanitized via
# tools/cell_sanitizer.sanitize_cell by EVERY exporter reachable from this path
# -- xlsx_generator, csv_exporter and testrail_exporter --
# before it reaches a spreadsheet cell).
# --------------------------------------------------------------------------- #

bind(sys.modules[__name__])
