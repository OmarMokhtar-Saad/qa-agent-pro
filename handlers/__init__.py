"""MCP handler layer: task-shaped, transport-agnostic wrappers over the QA
agents and tools (gated by QA_MCP_ENABLED at the server layer).

The handlers live in the submodules of this package, one per feature. This
package binds no re-exported names (P7S S2): import a name from the submodule
that defines it. The eager loads below keep the former shim's import side
effects (which first-party modules load, and in which order).
"""

from __future__ import annotations

__all__ = []

import importlib

_EAGER = (
    "config.settings",
    "tools",
    "tools.case_quality_gate",
    "tools.dispatch_guard",
    "tools.handover_cost",
    "tools.host_privileges",
    "tools.prep_store",
    "tools.step_assertion",
    "tools.telemetry",
    "tools.audit_log",
    "tools.build_fingerprint",
    "tools.csv_exporter",
    "tools.device_manager",
    "tools.gherkin_exporter",
    "tools.jira_fetcher",
    "tools.jira_mcp",
    "tools.playwright_exporter",
    "tools.rag_store",
    "tools.suite_quality_signals",
    "tools.suite_store",
    "tools.swagger_fetcher",
    "tools.testrail_exporter",
    "tools.tool_briefs",
    "tools.ui_extractor",
    "tools.untrusted",
    "tools.xlsx_generator",
)

for _m in _EAGER:
    importlib.import_module(_m)
del _m

from . import (  # noqa: E402, F401
    _agents,
    _common,
    _elicit,
    _reply,
    api,
    capture,
    corpus,
    explore_bug,
    export,
    feature_analysis,
    generate,
    generate_prepare,
    host_check,
    image_gate,
    jira,
    mobile_content,
    mobile_device,
    mobile_gate,
    mobile_run,
    mobile_step,
    prep_status,
    setup_check,
    submit_category,
    submit_suite,
    wizard,
)
