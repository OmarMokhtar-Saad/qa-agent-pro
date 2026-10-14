"""Moved from handlers (refactor P2): generate."""

# ruff: noqa: F401

from __future__ import annotations

import logging
import sys

from agents.host_ac import AC_JOB
from agents.host_checklist import CHECKLIST_JOB
from agents.host_gen_hooks import (
    CaptureHooks,
    CommonHooks,
    GenerateHooks,
    ImageHooks,
    JiraHooks,
    JobHooks,
    NoteHooks,
    RunHooks,
    StoreHooks,
)
from agents.host_gen_image_audit import (
    attach_nudge_text as _attach_nudge_text,
)
from agents.host_gen_image_audit import (
    capture_nudge_text as _capture_nudge_text,
)
from agents.host_gen_image_notes import captured_screens_note as _captured_screens_note
from agents.host_gen_notes import prepare_reply_notes as _prepare_reply_notes
from agents.host_gen_preflight import find_open_prep as _find_open_prep
from agents.host_gen_snapshot_a import jira_content_chars as _jira_content_chars
from agents.host_gen_snapshot_a import safe_snapshot_stamp as _safe_snapshot_stamp
from agents.host_gen_snapshot_a import snapshot_note as _snapshot_note
from agents.host_gen_snapshot_a import stale_snapshot_note as _stale_snapshot_note
from agents.host_gen_snapshot_b import condensed_payload_note as _condensed_payload_note
from agents.host_gen_types import (
    PreparePayloadResult,
    _Beat1,
    _GateCtx,
    _Grounding,
    _PreflightCarry,
    _PrepRun,
    _TextOnlySkip,
)
from agents.host_image import IMAGE_JOB, IMAGE_PREFLIGHT_JOB, IMAGE_RELEVANCE_JOB
from agents.host_jobs import attach_ambiguity_job, attach_jobs
from agents.host_orchestration import (
    build_prepare_payload,
    expected_category_names,
    prepared_case_bounds,
)
from agents.host_prep_serde import serialize_prepared
from agents.test_scenario_agent import _prepare_generation as prepare_generation
from handlers import (
    _common,
    capture,
    capture_limits,
    image_gate,
    jira,
    prep_status,
)
from handlers.generate_args import (
    Beat1Args,
    Beat2Args,
    GroundArgs,
    PlanNudgeArgs,
    PreflightArgs,
)
from handlers.generate_consts import _BEAT1_MISSING_HEAD, _BEAT1_MISSING_TAIL
from handlers.generate_gate_beat1 import (
    _beat1_attested_skip,
    _beat1_fire,
    image_gate_beat1,
)
from handlers.generate_gate_beat2 import (
    _evidence,
    _image_gate_beat2,
    image_plan_nudge,
)
from handlers.generate_ground import (
    _extract_page_ui,
    _fetch_page_content,
    _fetch_step,
    _grounding_fetch,
    _grounding_gates,
    _jira_gate,
    _openapi_text,
    _page_fetch_refusal,
    ground_and_gate,
)
from handlers.generate_hooks_a import (
    _capture_hooks,
    _common_hooks,
    _image_hooks,
    _note_hooks,
)
from handlers.generate_hooks_b import (
    _jira_hooks,
    _job_hooks,
    _run_hooks,
    _store_hooks,
    build_hooks,
)
from handlers.generate_host import bind
from handlers.generate_preflight import _carry_stamp, _prepare_preflight
from handlers.generate_wrappers import (
    _attested_image_count,
    _audit_image_plan_nudge,
    _finish_prepare,
    _host_mode_server_llm_notice,
    _image_gate_would_fire,
    _merge_carry_forward,
    _pending_image_gate_hint,
    _prepare_input_refusal,
    _run_prepare_generation,
    _ticket_image_note,
    _ticket_recency_notes,
    gen_duplicate_case_note,
    gen_persist_suite,
    gen_recent_suite_warning_note,
)
from tools.build_fingerprint import code_fingerprint
from tools.jira_fetcher import fetch_url_content
from tools.jira_mcp import issue_url_for_key
from tools.rag_store import add_to_corpus, query_corpus, replace_source_entries
from tools.suite_store import find_recent_suite_by_source, load_suite
from tools.swagger_fetcher import fetch_openapi_spec
from tools.ui_extractor import extract_ui_elements

# isort: split
# Re-exports kept for tests/handlers that patch or import `generate.<name>` (pinned).
from agents.host_gen_corpus_a import (
    _RECENT_SUITE_WARN_WINDOW_S,
)
from agents.host_gen_corpus_a import (
    case_content_hash as _case_content_hash,
)
from agents.host_gen_image_notes import (
    missing_captures_note as _missing_captures_note,
)
from agents.host_gen_issue_notes import (
    jira_page_without_issue_note as _jira_page_without_issue_note,
)
from agents.host_gen_issue_notes import (
    open_prep_refusal as _open_prep_refusal,
)
from agents.host_gen_jobs import (
    checklist_job_on as _checklist_job_on,
)
from agents.host_gen_jobs import (
    host_forward_images as _host_forward_images,
)
from agents.host_gen_jobs import (
    image_job_flags as _image_job_flags,
)
from agents.host_gen_jobs import (
    prospective_images as _prospective_images,
)
from agents.host_gen_jobs import (
    rule_pack_narrowed as _rule_pack_narrowed,
)
from agents.host_gen_notes import (
    ago_label as _ago_label,
)
from agents.host_gen_pipeline_a import (
    image_intake as _image_intake,
)
from agents.host_gen_snapshot_a import (
    parse_jira_timestamp as _parse_jira_timestamp,
)
from agents.host_gen_snapshot_b import (
    _CONDENSED_MAX_RATIO,
    _CONDENSED_MIN_DELTA,
)
from agents.host_gen_types import (
    _ImageFlags,
    _ImageIntake,
    _TicketNotes,
)

logger = logging.getLogger("tools.mcp_handlers")

bind(sys.modules[__name__])
