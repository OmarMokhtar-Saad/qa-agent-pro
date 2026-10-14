"""Facade of the host duplicate-review pass.

The implementation lives in the flat ``agents/host_dedup_*.py`` modules; every
name callers or tests reach through here is re-exported from its home. A test
that patches a name patches it on that home module (the one whose code looks it
up at call time).
"""

from __future__ import annotations

import difflib  # noqa: F401
import logging  # noqa: F401
import re  # noqa: F401
from dataclasses import dataclass  # noqa: F401

from agents.host_dedup_agree import (  # noqa: F401
    _dup_text,
    _dup_text_ratio,
    _group_agreement,
    _group_indices,
    dup_agreements,
)
from agents.host_dedup_apply import (  # noqa: F401
    _log_removed,
    _plan_drops,
    _rescue_sole_requirements,
    apply_duplicate_groups,
)
from agents.host_dedup_consts import (  # noqa: F401
    _DUP_LOW_TEXT_DEFAULT,
    _DUP_MAX_APPLY_GROUP_SIZE,
    _DUP_MAX_GROUP_SIZE,
    _DUP_MAX_GROUPS,
    _DUP_PRIORITY_RANK,
    _DUP_REMOVAL_RATIO_CEILING,
    _DUP_REMOVAL_RATIO_DEFAULT,
    _DUP_WS_RE,
    _MAX_DUP_NOTES,
    _MAX_DUP_REMOVED_IDS,
    _MAX_DUP_SECTION_CHARS,
    _UNTRUSTED_ERRORS,
    logger,
)
from agents.host_dedup_contrast import (  # noqa: F401
    _DUP_DISCRIMINATORS,
    _is_boundary_contrast,
)
from agents.host_dedup_extract import (  # noqa: F401
    _append_note,
    _collect_group_members,
    _extract_duplicate_groups,
    _walk_duplicate_groups,
)
from agents.host_dedup_headline import (  # noqa: F401
    _headline_cap_note,
    build_dup_contradiction_headline,
)
from agents.host_dedup_intro import (  # noqa: F401
    _dup_review_intro_lines,
    _dup_truncation_lines,
)
from agents.host_dedup_limits import (  # noqa: F401
    _dup_group_caps,
    _dup_keeper_key,
    _low_text_ratio,
    _removal_ratio,
)
from agents.host_dedup_lines import (  # noqa: F401
    DupReviewOutcome,
    DupSuites,
    _dup_notes_lines,
    _dup_review_lines,
    _member_renderer,
    _render_dup_group_lines,
    _render_dup_member,
)
from agents.host_dedup_pairs import (  # noqa: F401
    _DUP_SHORTLIST_MAX_CASES,
    _DUP_SHORTLIST_MAX_PAIRS,
    _DUP_SHORTLIST_TITLE_CHARS,
    _DUP_SHORTLIST_TITLE_SCAN,
    _DUP_SHORTLIST_TOKEN_RE,
    DUP_SHORTLIST_MIN_RATIO,
    _pair_ratio,
    _shortlist_entries,
    _shortlist_pairs,
    dup_shortlist_on,
    dup_title_tokens,
)
from agents.host_dedup_render import (  # noqa: F401
    build_dup_contradiction_pairs,
    build_dup_shortlist_section,
    shortlist_safe,
)
from agents.host_dedup_report import (  # noqa: F401
    _resolve_outcome,
    build_duplicate_section,
)
from agents.host_dedup_screen import (  # noqa: F401
    _removal_refusal,
    _screen_group_sizes,
    screen_duplicate_groups,
)
from agents.host_dedup_shortlist import (  # noqa: F401
    build_dup_shortlist,
    build_dup_shortlist_counted,
    dup_shortlist_cases_json,
)
from tools.rtm import normalize_ac_id  # noqa: F401

# Pinned readers elsewhere (WX retires): by-value aliases of the public names.
_dup_title_tokens = dup_title_tokens
