"""Requirements Traceability Matrix helpers.

Parses acceptance criteria text into numbered AcceptanceCriterion items
and builds a markdown RTM coverage summary.

Never raises — all functions return empty results on failure.
"""

from __future__ import annotations

import logging
import re  # noqa: F401
from dataclasses import dataclass  # noqa: F401

from tools.models import TestCase, normalize_ac_id  # noqa: F401
from tools.rtm_cells import (  # noqa: F401
    _coverage_suppressed_row,
    _criterion_rows,
    _linked_cell,
    _provenance_row,
    _suite_has_no_links,
    _untraced_row,
)
from tools.rtm_consts import (  # noqa: F401  (stale copies; readers use _k.<NAME>)
    _AC_BLOCK_LIMIT,
    _AC_DESCRIPTION_LIMIT,
    _NOT_REPORTED,
    _RTM_DESC_CAP,
    _RTM_LINKED_CAP,
)
from tools.rtm_labels import (  # noqa: F401
    _AC_LABEL_RE,
    _AC_SPLIT_RE,
    _ANY_LABEL_RE,
    _BARE_SECTION_RE,
    _INLINE_LABEL_SPLIT_RE,
    _LABEL_BODY,
    _MIN_INLINE_LABELS,
    _NON_AC_PREFIX_RE,
    _leading_label,
    _norm_label,
)
from tools.rtm_line import _oneline_tail, rtm_oneline  # noqa: F401
from tools.rtm_looks import (  # noqa: F401
    _ISO_DATEISH_RE,
    _NUMERIC_RE,
    _SLASH_DATEISH_RE,
    looks_like_requirement_text,
)
from tools.rtm_parse import parse_acceptance_criteria  # noqa: F401
from tools.rtm_prompt import (  # noqa: F401
    _AC_PROMPT_HEAD,
    _AC_PROMPT_NULL_CLAUSE,
    format_ac_prompt_block,
)
from tools.rtm_rows import _coverage_row, rtm_rows  # noqa: F401
from tools.rtm_sanitize import (  # noqa: F401
    AcceptanceCriterion,
    sanitize_ac_description,
)
from tools.rtm_split import _accept_ac_candidate, _split_ac_lines  # noqa: F401
from tools.rtm_summary import _summary_table, build_rtm_summary  # noqa: F401
from tools.rtm_tracemap import _trace_map, orphan_case_ids, rtm_trace  # noqa: F401
from tools.rtm_warning import (  # noqa: F401
    _DEGENERATE_HEAD,
    _lone_ac_warning,
    traceability_warning_section,
)
from tools.untrusted import strip_spoof_tags, wrap_untrusted  # noqa: F401

logger = logging.getLogger(__name__)

# `_NLI_LEDGER_ID = "rtm.nli_verdicts"` stood here until 2026-08-16 (dead-code
# deletion P2-G1), together with the two OPTIONAL LLM tiers it tagged. Its
# sibling `_AC_LEDGER_ID` ("rtm.acceptance_criteria") went in P2-F2. Both ids
# STAY in tools/host_llm.LEDGER_IDS -- that frozenset never shrinks, because an
# id is what keeps "this path migrated / was disabled" checkable after the code
# is gone. There is nothing left in this module to tag: it makes no LLM call.


# generate_acs lived here until 2026-08-16 (dead-code deletion P2-F2),
# together with its _GeneratedAC / _GeneratedACList response models, the
# _AC_GEN_SYSTEM prompt and the ledger id `rtm.acceptance_criteria`. It made
# ONE server-side llm.ask_json call synthesizing acceptance criteria for the
# 3-of-4 input types that carry none, so the RTM could light up.
#
# It was dead. Its only caller was agents/test_scenario_agent._run_gen_acs,
# which ran only under `synthesize_acs=True`; the one live caller of
# _prepare_generation (handlers.handle_prepare_test_cases) passed
# synthesize_acs=not _host_ac, and `_host_ac` derives from
# llm.resolve_generation_mode() == "host" -- a constant since 2026-08-12. The
# legacy routes that still reached it, graph.py and evals/, were deleted in
# P2-A and P2-B.
#
# There is NO capability loss: the criteria are derived by the tester's OWN
# model as agents/host_ac.AC_JOB, and _prepare_generation still appends
# _HOST_AC_JOB_DIRECTIVE to rtm_hint to ask for them. parse_acceptance_criteria,
# rtm_trace and the rest of this module are LIVE and untouched.


# ``normalize_ac_id`` MOVED to tools/models.py on 2026-08-19 (F06) and is
# re-exported by the import above, so `from tools.rtm import normalize_ac_id`
# still resolves for tools/ac_anchor.py, agents/host_*.py,
# agents/test_scenario_agent.py and tests/test_rtm.py -- none of them changed.
# It moved because its sibling ``display_requirement_id`` is needed by all five
# exporters on every row, and importing THIS module for it would have pulled
# dependencies the exporters don't need.
