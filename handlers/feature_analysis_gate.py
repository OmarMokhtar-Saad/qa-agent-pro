"""The edition / feature gate both Feature Analysis handlers repeat."""

from __future__ import annotations

from handlers import _common, export


def _fa_disabled_reply() -> str:
    """The refusal when this build offers no Feature Analysis, else ""."""
    # Defence in depth (2026-08-03): mcp_server.py does not REGISTER this tool
    # in the test-cases-only edition, so this handler is unreachable over MCP
    # there -- but that edition is credential-free by design and the mobile
    # modes below call this server's own vision, so the handler refuses on its
    # own too rather than depending on one registration gate. Same shape as
    # handle_bug_report / handle_explore_step.
    if _common.test_cases_only():
        return export._TEST_CASES_ONLY_NOTICE
    return ""
