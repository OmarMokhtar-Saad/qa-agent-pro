"""Enterprise Feature Analysis Report (opt-in).

A structured, LLM-generated report that merges Jira ticket content with mobile
screenshot descriptions into a single enterprise-QA "Feature Analysis Report",
produced ALONGSIDE the normal test-case suite by generate_test_scenarios when
the feature is on (unconditional since 2026-08-14, batch 8c).

House rules honoured: this module imports no routing or handler layer (the
``router.py`` the rule used to name was deleted in P2-A, 2026-08-15; what the
rule protects now is that ``agents/`` never imports ``handlers/``,
so an agent can be exercised without the MCP transport); secrets come from
``.env`` via config.settings only; no
bare ``print()`` -- logging only. Never raises to its caller: ``analyze_feature``
returns an empty ``FeatureAnalysisReport`` on any failure, mirroring
``critique_coverage`` in test_scenario_agent.py.

Prompt-injection containment: every externally-sourced segment fed to the model
(Jira/feature text, parsed acceptance criteria, screenshot descriptions, and
extracted UI structure) is wrapped with ``tools.untrusted.wrap_untrusted`` and
the ``_GUARD`` note is appended to the system prompt at call time -- exactly the
contract the rest of test_scenario_agent.py already uses.
"""

from __future__ import annotations

import logging

# No llm.* import survives here: analyze_feature was this module's only
# server-side call and it was deleted on 2026-08-16 (P2-E3). What remains is
# prompt building, host-task brokering and rendering -- chat-only end to end.
from agents.feature_analysis_build import (  # noqa: F401
    _round_prompt,
    build_feature_analysis_prompt,
)
from agents.feature_analysis_coerce import finalize_feature_report  # noqa: F401
from agents.feature_analysis_model import FeatureAnalysisReport
from agents.feature_analysis_render import render_report_markdown  # noqa: F401
from agents.feature_analysis_request import FeatureAnalysisRequest, _task_meta
from tools.host_llm import HostTaskRequest, open_task

logger = logging.getLogger(__name__)

# This module's row in docs/LLM_MIGRATION_INVENTORY.md. The STANDALONE
# qa_feature_analysis tool is CHAT-ONLY as of 2026-08-02 (qa_feature_analysis ->
# qa_submit_feature_analysis via tools/host_llm) and never reaches a backend.
# The LEGACY analyze_feature below survives for _finalize_generation's opt-in
# in-prep report and tags its call with this id.
_LEDGER_ID = "feature_analysis.report"

# analyze_feature() lived here until 2026-08-16 (dead-code deletion P2-E3). It
# was this module's ONLY server-side call -- one structured ask_json under the
# `feature_analysis.report` ledger id -- and its only caller was the inline
# report inside _finalize_generation, deleted in the same batch. That branch was
# unreachable twice over: the sole surviving caller of _finalize_generation
# passes feature_report_enabled=False, and force_feature_report had reached
# nothing since 41e0ec5 removed the fall-through that forwarded it.
#
# The TOOL is unaffected. qa_feature_analysis and qa_submit_feature_analysis are
# chat-only and use build_feature_analysis_prompt (above),
# prepare_feature_analysis, finalize_feature_report and render_report_markdown
# (below) -- none of which makes a server-side call. The `feature_analysis.report`
# id stays in tools/host_llm.LEDGER_IDS: that frozenset never shrinks.


# No backend is contacted. The server still does everything only it can do --
# hardened Jira fetching, device capture, untrusted wrapping -- and, in the
# submit half, the coercion and the rendering. The prompt INPUTS ride on the
# task RECORD, never in the envelope, so a resubmit round can rebuild the
# prompt without re-driving the mobile capture loop and a host cannot alter
# them. Returns the house ``{"error", "content": {"task_id", "envelope"}}``.
async def prepare_feature_analysis(request: FeatureAnalysisRequest) -> dict:
    """Open a host task asking the tester's own chat model to write the report; never raises."""
    try:
        system, user = _round_prompt(request)
        return await open_task(
            HostTaskRequest(
                "feature_analysis",
                system,
                user,
                return_field="report",
                response_schema=FeatureAnalysisReport.model_json_schema(),
                meta=_task_meta(request),
                submit_tool=request.submit_tool,
            )
        )
    except Exception as exc:
        logger.exception("prepare_feature_analysis failed")
        return {"error": str(exc), "content": None}
