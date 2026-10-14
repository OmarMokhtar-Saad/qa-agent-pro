"""Facade of the chat-only test-scenario pipeline.

The implementation lives in the ``agents.scenario_*`` modules; this module
re-exports the names tests and callers read, and keeps the public alias
``finalize_generation``.

History kept here (the code is gone, the decisions stand):

- prompt_cache_enabled() and the prompt-cache warm-up were deleted with the
  dead-code pass (2026-08-16); llm.py keeps its own half of that seam.
- feature_analysis_enabled() governed nothing once the inline report branch
  and analyze_feature went; the seams are now deleted (WD), and agents/feature_analysis.py keeps what the chat-only
  qa_feature_analysis tools use.
- The Jira ticket-image vision call (_describe_ticket_images) was dead: the
  only caller passes describe_images_server_side=False, and the raw bytes
  ride to the tester's own model through agents/host_image.IMAGE_JOB.
- The three residue sub-phase R2 ledger rows (server_fanout, rewrite_vague,
  markdown) name no code in this module any more, and the constants that
  carried their ids are gone. The ids stay in tools/host_llm.LEDGER_IDS,
  a frozenset that never shrinks, because that keeps "migrated / disabled"
  checkable after the implementation is deleted.
"""

from __future__ import annotations

import asyncio  # noqa: F401
import logging

from agents.scenario_args import (  # noqa: F401
    GenerationRequest,
    GroundingSource,
    RtmFacts,
)
from agents.scenario_bounds import _case_count_bounds  # noqa: F401
from agents.scenario_categories import (  # noqa: F401
    _EDGE_CASES_INDEX,
    CATEGORIES,
    CategoryResult,
    effective_categories,
)
from agents.scenario_dedupe import _dedupe_cases  # noqa: F401
from agents.scenario_finalize import _finalize_generation
from agents.scenario_finalize_steps import (  # noqa: F401
    _dedupe_for_finalize,
    _renumber_and_normalize,
    _score_and_scope,
)
from agents.scenario_grounding import (  # noqa: F401
    grounding_sections,
    target_source_text,
)
from agents.scenario_prepare import _prepare_generation  # noqa: F401
from agents.scenario_prepared import (  # noqa: F401
    _HOST_AC_JOB_DIRECTIVE,
    PreparedGeneration,
)
from agents.scenario_prompt_rules import (  # noqa: F401
    _QUALITY_RULES_BODY,
    _QUALITY_RULES_UPFRONT,
    _STEP_SHAPE_EXAMPLE,
    _TEST_DATA_INSTRUCTION,
    _category_shared_system,
)
from agents.scenario_prompt_text import (  # noqa: F401
    _CATEGORY_HEADER,
    _CATEGORY_JSON_TAIL,
    _CATEGORY_RULES,
    _CATEGORY_TASK_TEMPLATE,
)
from agents.scenario_rtm import _build_rtm_hint, _source_acs  # noqa: F401
from agents.scenario_summary import _report_sections, _summary_head  # noqa: F401
from agents.scenario_suppression import _host_suppression_section  # noqa: F401

logger = logging.getLogger(__name__)

finalize_generation = _finalize_generation
