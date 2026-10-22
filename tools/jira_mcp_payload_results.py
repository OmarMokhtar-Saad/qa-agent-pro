"""Jira MCP: payload results helpers (split from tools/jira_mcp.py)."""

from __future__ import annotations

from tools.jira_mcp_directive import jira_mcp_required_result
from tools.jira_mcp_errors import _jira_failure_reason
from tools.jira_mcp_tool_names import fetch_tool_name


def _payload_failure_result() -> dict:
    """The never-raise refusal for a payload that blew up mid-normalisation."""
    return {
        "error": (
            "⚠️ I couldn't process that Jira payload. Re-run the "
            "`getJiraIssue` call and pass its RAW JSON result unmodified, or "
            "paste the ticket text and I'll generate test cases from it."
        ),
        "content": None,
        "needs_jira_mcp": False,
    }


def _refusal(error: str, needs_mcp: bool = False) -> dict:
    """The early-return dict shape shared by the payload refusals."""
    return {"error": error, "content": None, "needs_jira_mcp": needs_mcp}


# B4 (2026-09-26, v1.98 scope B): the generic wrap below built
# a garbled composite ("I couldn't read the payload (That
# Jira payload is too large...)") whose own instruction then
# told the agent to RESEND the same oversized payload
# unmodified -- guaranteed to fail identically again. Name
# the size (already inside load_error) and the real escape
# hatch: staged parts, not a retry of the thing just refused.
def _too_large_result(load_error: str) -> dict:
    """Refusal: the payload is over the size cap; point at staged parts."""
    return _refusal(
        f"⚠️ {load_error} Sending it again unmodified will "
        "fail the same way -- switch to staged parts instead. "
        "Call `qa_prepare_test_cases` again with the SAME "
        "`feature_or_url` and NO `jira_content_json` to get a "
        f"fresh `stage_token`, then call `{fetch_tool_name()}` "
        "for each part you still need and stage that ONE "
        "result with `qa_stage_jira(stage_token=..., "
        "part='issue'|'parent'|'siblings', json=<that "
        "result>)` -- for a result over 20 KB save it to a file and "
        "pass `path=<absolute file>` instead of `json` -- then "
        "call `qa_prepare_test_cases` again with that SAME "
        "`stage_token`."
    )


def _load_error_result(load_error: str, source_url: str) -> dict:
    """The early-return dict for a payload ``load_payload`` refused."""
    if "too large" in load_error:
        return _too_large_result(load_error)
    return _refusal(
        "⚠️ I couldn't read the Jira payload you sent back "
        f"({load_error}). Re-run the `getJiraIssue` call and pass its "
        "RAW JSON result as `jira_content_json`, unmodified."
        if load_error not in ("empty",)
        else jira_mcp_required_result(source_url)["error"],
        load_error == "empty",
    )


# A payload with no `fields` is USUALLY a trimmed result -- but it is
# also exactly what Jira returns when the caller cannot READ the
# issue, and telling that tester to "call getJiraIssue again" is an
# instruction to repeat a call that will fail the same way. Say what
# Jira actually said when Jira said anything.
def _no_fields_result(payload: dict, issue: object) -> dict:
    """The early-return dict for a payload that carries no ``fields`` object."""
    reason = _jira_failure_reason(payload) or _jira_failure_reason(
        issue if isinstance(issue, dict) else {}
    )
    return _refusal(
        f"⚠️ Jira did not return that issue: {reason}. Check the "
        "issue key and that your Atlassian account can see it, then "
        "re-run `getJiraIssue`."
        if reason
        else "⚠️ That Jira payload has no `fields` object, so there "
        "is nothing to generate from. Call `getJiraIssue` again and pass "
        "its RAW result (the whole object, including `fields`)."
    )


def _empty_source_result(any_truncated: bool) -> dict:
    """The early-return dict for an issue with no summary, description or AC."""
    return {
        "error": (
            "⚠️ That Jira issue's text is nested deeper than I parse, "
            "so none of it reached me and there is nothing to generate "
            "from. Paste the ticket text and I'll work from that."
            if any_truncated
            else "⚠️ That Jira issue came back with no summary, no "
            "description and no acceptance criteria, so there is nothing to "
            "generate from. Check the issue key, or paste the ticket text "
            "and I'll work from that."
        ),
        "content": None,
        "needs_jira_mcp": False,
    }
