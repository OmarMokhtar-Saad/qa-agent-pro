"""Moved from handlers (refactor P2): submit_suite."""

from __future__ import annotations

import logging
import sys
import types  # noqa: F401
from typing import Any  # noqa: F401

from agents import (
    host_submit_ambiguity,  # noqa: F401
    host_submit_dup_notes,  # noqa: F401
    host_submit_images,  # noqa: F401
    host_submit_read,  # noqa: F401
    host_submit_sidecar,  # noqa: F401
)
from agents.host_ac import build_host_ac_section, extract_host_acs  # noqa: F401
from agents.host_checklist import (  # noqa: F401
    build_host_checklist_section,
    extract_host_checklist,
)
from agents.host_dedup import (
    apply_duplicate_groups,  # noqa: F401
    build_dup_contradiction_headline,  # noqa: F401
    build_dup_contradiction_pairs,  # noqa: F401
    build_dup_shortlist_counted,  # noqa: F401
    build_duplicate_section,  # noqa: F401
    dup_shortlist_cases_json,  # noqa: F401
    screen_duplicate_groups,  # noqa: F401
)
from agents.host_dedup import dup_agreements as host_dedup_dup_agreements  # noqa: F401
from agents.host_grounding import (  # noqa: F401
    build_grounding_section,
    route_ungrounded_cases,
)
from agents.host_image import (
    build_host_image_section,  # noqa: F401
    extract_host_image_descriptions,  # noqa: F401
    image_relevance_counts,  # noqa: F401
)
from agents.host_jobs import (  # noqa: F401
    build_ambiguity_result_section,
    extract_ambiguity_result,
)
from agents.host_prep_serde import deserialize_prepared  # noqa: F401
from agents.host_submission import parse_host_suite, safe_prep_id  # noqa: F401
from agents.host_submit_ambiguity import AmbRefusalIn  # noqa: F401
from agents.host_submit_pipeline import run_submit_suite  # noqa: F401
from agents.host_submit_pipeline_run import SubmitCall  # noqa: F401
from agents.host_submit_ports_prep import SubmitPorts  # noqa: F401
from agents.host_submit_push import PushCall, run_push_suite  # noqa: F401
from agents.host_submit_types import SubAcks as _SubAcks  # noqa: F401
from agents.host_submit_types import SubRead as _SubRead  # noqa: F401
from agents.host_submit_types import SubReq as _SubReq  # noqa: F401
from agents.test_scenario_agent import (
    finalize_generation,  # noqa: F401
)
from handlers import (
    _common,  # noqa: F401
    _elicit,  # noqa: F401
    _reply,  # noqa: F401
    export,  # noqa: F401
    generate,  # noqa: F401
    generate_prepare,  # noqa: F401
    prep_status,  # noqa: F401
    submit_category,  # noqa: F401
)
from handlers.submit_suite_cports import (
    _category_port,  # noqa: F401
    _common_port,  # noqa: F401
    _elicit_port,  # noqa: F401
    _image_port,  # noqa: F401
    _ports,  # noqa: F401
    _prep_gate_port,  # noqa: F401
    _prep_load_port,  # noqa: F401
    _reply_port,  # noqa: F401
)
from handlers.submit_suite_handle import (
    PushOptions,  # noqa: F401
    SubmitOptions,  # noqa: F401
    _guarded_submit,  # noqa: F401
    handle_push_suite,  # noqa: F401
    handle_submit_suite,  # noqa: F401
)
from handlers.submit_suite_host import bind
from handlers.submit_suite_ports import (
    _attrs,  # noqa: F401
    _dup_port,  # noqa: F401
    _io_port,  # noqa: F401
    _media_port,  # noqa: F401
    _out_port,  # noqa: F401
    _review_port,  # noqa: F401
)
from handlers.submit_suite_wrap import (
    _ambiguity_refusal,  # noqa: F401
    _dup_status_note,  # noqa: F401
    _image_gate_verdict,  # noqa: F401
    _read_full_suite,  # noqa: F401
    _read_via_sidecar,  # noqa: F401
    _sidecar_no_rows_reply,  # noqa: F401
    sidecar_merge,  # noqa: F401
    try_retain_sidecar,  # noqa: F401
)
from tools import (
    dispatch_guard,  # noqa: F401
)
from tools.build_fingerprint import code_fingerprint  # noqa: F401
from tools.suite_store import (  # noqa: F401
    earlier_suites,
    family_of,
    load_suite,
    save_checklist,
    save_suite,
    set_export_path,
)

logger = logging.getLogger("tools.mcp_handlers")

bind(sys.modules[__name__])
