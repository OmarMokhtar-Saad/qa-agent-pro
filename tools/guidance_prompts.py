"""Prompt table and edition gate sets (leaf of tools/guidance.py)."""

from __future__ import annotations

from tools.guidance_p_bug import _P_API, _P_BUG, _P_EXPLORE
from tools.guidance_p_flow import _P_GENERATE, _P_JIRA
from tools.guidance_p_mobile import _P_MOBILE

_PROMPTS: dict[str, tuple[str, str]] = {
    "qa_generate": ("Generate a full test suite for a feature", _P_GENERATE),
    "qa_jira_to_tests": ("Generate a test suite from a Jira ticket", _P_JIRA),
    # NOT `qa_bug_report`: that is the TOOL's name. Prompts and tools are
    # separate MCP registries, so the protocol allows the clash -- but a
    # client picker lists both, and two identical rows with different
    # behaviour is a trap for the tester. Every other prompt name here
    # already differs from every tool name; the test pins that.
    "qa_bug_report_workflow": ("Write a structured bug report", _P_BUG),
    "qa_explore": ("Run a guided exploratory testing session", _P_EXPLORE),
    "qa_api_tests": (
        "Generate REST-Assured API tests",
        _P_API,
    ),  # NOT `qa_mobile_test`, for the reason recorded above `qa_bug_report_workflow`:
    # a prompt sharing a tool's name puts two identical rows with different
    # behaviour in the client's picker. Pinned by the same test.
    "qa_mobile_run": ("Run tests on an Android emulator", _P_MOBILE),
}

#: Prompts that name a tool only the FULL edition registers.
_FULL_ONLY = frozenset({"qa_bug_report_workflow", "qa_explore"})

#: Prompts that name a tool registered only when the API test agent is on.
_API_ONLY = frozenset({"qa_api_tests"})
#: Prompts that name a tool registered only when the mobile lane is on.
_MOBILE_ONLY = frozenset({"qa_mobile_run"})
