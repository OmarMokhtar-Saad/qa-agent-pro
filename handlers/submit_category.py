"""Moved from handlers (refactor P2): submit_category -- the public facade.

The handler and its helpers live in the ``submit_category_*`` modules; every
name the old module bound is re-bound here so callers and patches keep working.
"""

from __future__ import annotations

import json  # noqa: F401
import logging  # noqa: F401
from typing import (
    Any,  # noqa: F401
    NamedTuple,  # noqa: F401
)

from agents.host_category_jobs import (
    category_checklist_note,  # noqa: F401
    category_dedup_note,  # noqa: F401
    normalize_category,  # noqa: F401
    prep_status_view,  # noqa: F401
)
from agents.host_dedup import (
    build_dup_shortlist,  # noqa: F401
    build_dup_shortlist_section,  # noqa: F401
    dup_shortlist_on,  # noqa: F401
)
from agents.host_jobs import ambiguity_notes_gen_note  # noqa: F401
from agents.host_prep_serde import PrepSerdeError  # noqa: F401
from agents.host_submission import (
    parse_host_suite,  # noqa: F401
    safe_prep_id,  # noqa: F401
)
from handlers import (
    _common,  # noqa: F401
    _elicit,  # noqa: F401
    _reply,  # noqa: F401
    export,  # noqa: F401
    generate,  # noqa: F401
    generate_prepare,  # noqa: F401
    prep_status,  # noqa: F401
)
from handlers._boundary import handler_guard  # noqa: F401
from handlers.submit_category_ambiguity import (
    ambiguity_gen_notes,  # noqa: F401
)
from handlers.submit_category_args import (
    _category_submit_args,  # noqa: F401
)
from handlers.submit_category_assertion import (
    _assertion_ack_shape,  # noqa: F401
    _assertion_refusal_text,  # noqa: F401
    _mark_assertion_refused,  # noqa: F401
    step_assertion_gate,  # noqa: F401
)
from handlers.submit_category_dup import (
    _category_dup_note,  # noqa: F401
    dup_shortlist_note,  # noqa: F401
)
from handlers.submit_category_gen_notes import (
    _skipped_attachments_gen_notes,  # noqa: F401
    checklist_gap_gen_notes,  # noqa: F401
    early_gen_notes,  # noqa: F401
    quality_advisory_gen_notes,  # noqa: F401
    step_assertion_gen_notes,  # noqa: F401
)
from handlers.submit_category_hint import (
    _staged_names_for_hint,  # noqa: F401
    _staged_row_count,  # noqa: F401
    staged_resubmit_hint,  # noqa: F401
)
from handlers.submit_category_intake import (
    _category_intake,  # noqa: F401
)
from handlers.submit_category_oversized import (
    oversized_submission_note,  # noqa: F401
)
from handlers.submit_category_record import (
    _category_audit_detail,  # noqa: F401
    _record_category,  # noqa: F401
    _stage_category,  # noqa: F401
    handle_submit_category,  # noqa: F401
)
from handlers.submit_category_reply import (
    _category_notes,  # noqa: F401
    _category_reply,  # noqa: F401
    _render_category_reply,  # noqa: F401
)
from handlers.submit_category_resubmit import (
    _replaced_larger_row_note,  # noqa: F401
    _resubmit_delta_text,  # noqa: F401
    category_resubmit_note,  # noqa: F401
)
from handlers.submit_category_route import (
    _CATEGORY_PRIMARY_BULLET,  # noqa: F401
    _CATEGORY_REVIEW_LABEL,  # noqa: F401
    _CATEGORY_ROUTE_REVIEW,  # noqa: F401
    _CATEGORY_SIDECAR_LINE,  # noqa: F401
    _category_route,  # noqa: F401
)
from handlers.submit_category_shrink import (
    _prior_category_count,  # noqa: F401
    _refuse_shrinking_resubmit,  # noqa: F401
    shrinking_resubmit_reply,  # noqa: F401
)
from handlers.submit_category_types import (
    _CategoryArgs,  # noqa: F401
    _CategoryIntake,  # noqa: F401
    _CategoryStaged,  # noqa: F401
    _StagedView,  # noqa: F401
)
from handlers.submit_category_view import (
    _staged_category_view,  # noqa: F401
    all_staged_banner,  # noqa: F401
)
from handlers.submit_category_volume import (
    volume_floor_gate,  # noqa: F401
)
from tools import (
    dispatch_guard,  # noqa: F401
    prep_store,  # noqa: F401
    step_assertion,  # noqa: F401
)
from tools.error_boundary import text_reply  # noqa: F401
from tools.suite_quality_signals import compute_suite_quality_advisories  # noqa: F401

logger = logging.getLogger("tools.mcp_handlers")
