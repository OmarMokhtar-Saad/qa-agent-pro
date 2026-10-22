"""Jira MCP: directive parts helpers (split from tools/jira_mcp.py)."""

from __future__ import annotations

from typing import NamedTuple
from urllib.parse import urlparse

from tools import settings_port
from tools.jira_mcp_connect import not_connected_message


class DirectiveCtx(NamedTuple):
    """The per-directive values step 1 needs (param object for _directive_head)."""

    prefix: str
    token: str
    want_comments: bool
    ac_field: str


def _step_parent(prefix: str, token: str, step: int) -> str:
    """The parent-fetch step of the directive."""
    return (
        f"{step}. If the result has a `fields.parent`, call "
        f"`{prefix}getJiraIssue` a SECOND time for that parent key "
        "(its description usually holds the real requirements). "
        "If there is NO `fields.parent` but `fields.issuelinks` names "
        "an issue this one *implements* (or that it *is implemented "
        "by*), fetch THAT issue the same way and pass it as "
        "`parent_issue` -- on a backend/task ticket the requirement "
        "usually lives there, and its one-line link title is not "
        "enough. Send at most one. Immediately call "
        f"`qa_stage_jira(stage_token='{token}', part='parent', "
        "json=<STRINGIFIED result of THIS fetch only>)` (or, if your host spilled this result to a file, `path=<absolute file>` instead of retyping it) before doing "
        "anything else."
    )


def _step_sibling(prefix: str, token: str, step: int) -> str:
    """The sibling-search step of the directive."""
    return (
        f"{step}. If there WAS a parent, also call "
        f"`{prefix}searchJiraIssuesUsingJql` with `jql` = "
        '"parent = <THAT PARENT KEY> ORDER BY key", `fields` = '
        "[`summary`, `status`], `responseContentFormat` = "
        f'"markdown", `searchResultMode` = "all" '
        f"and `maxResults` = "
        f"{settings_port.get().jira_max_sibling_stories}. Pass the "
        f"whole result object back (its `total` lets me "
        f"say how many stories I did NOT read). The "
        "sibling user stories under that parent carry requirements this "
        "ticket inherits, and their one-line titles alone are not "
        "enough. Skip this step when there is no parent. Immediately "
        f"call `qa_stage_jira(stage_token='{token}', part='siblings', "
        "json=<STRINGIFIED result of THIS fetch only>)` (or `path=<absolute file>` if your host spilled the result to a file - never retype a large result) when you do "
        "run it."
    )


def _directive_optional_steps(prefix: str, token: str, want_parent: bool) -> list[str]:
    """Steps 2.. of the directive: the parent fetch and the sibling search."""
    lines: list[str] = []
    if want_parent:
        lines.append(_step_parent(prefix, token, 2))
        if settings_port.get().jira_fetch_sibling_stories:
            lines.append(_step_sibling(prefix, token, 3))
    return lines


def _fetch_step(ctx: DirectiveCtx, target: str) -> str:
    """Step 1: fetch the issue and stage it."""
    return (
        f"1. Call `{ctx.prefix}getJiraIssue` for {target} with `fields` "
        "including `summary,description,priority,labels,components,"
        "issuetype,status,updated,parent,subtasks,issuelinks,attachment"
        + (",comment" if ctx.want_comments else "")
        + (f",{ctx.ac_field}" if ctx.ac_field else "")
        + "`. Immediately call `qa_stage_jira(stage_token='"
        + ctx.token
        + "', part='issue', json=<STRINGIFIED result of THIS fetch "
        "only>)` before doing anything else - do not wait to collect "
        "the parent or siblings. If the result is over 20 KB, save it "
        "to a file (or use the file your host already spilled it to) and "
        "pass `path=<absolute file>` instead of `json`."
    )


def _step_zero(url: str) -> list[str]:
    """Step 0: confirm the `atlassian` MCP is connected before anything else."""
    return [
        "0. FIRST confirm the `atlassian` MCP server is connected: its tools "
        "must exist in your tool list and the first call below must succeed. "
        "If the tools do not exist, or that call fails with an auth error, do "
        "NOT guess the ticket contents and do NOT generate test cases from "
        "the URL alone - STOP and show the user this instead:",
        "",
        not_connected_message((urlparse(url).hostname or "")),
        "",
    ]


def _directive_head(ctx: DirectiveCtx, target: str, url: str = "") -> list[str]:
    """The opening of the fetch directive: step 0, the auth hard-stop, step 1."""
    return [
        "\U0001f517 **Fetch this Jira ticket with your own Atlassian MCP "
        "connection, then call me back.**",
        "",
        "This server no longer holds Jira credentials - you do, via OAuth. "
        "Do this now (a read-only fetch; no need to ask first). If the call "
        "fails with an auth error (401/403, not connected, token expired), "
        "STOP and tell the user: authenticate `atlassian` in your editor "
        "(Cursor: MCP settings -> Connect; Claude Code: /mcp), then retry. "
        "Never read tokens, keychains, credential files, or other "
        "workspaces' files to get around it:",
        "",
        *_step_zero(url),
        _fetch_step(ctx, target),
    ]


def _tail_step(step: int, token: str) -> str:
    """The closing step: call qa_prepare_test_cases again with the stage token."""
    return (
        f"{step}. Call `qa_prepare_test_cases` again with the SAME "
        "`feature_or_url` plus `stage_token='"
        + token
        + "'` - do NOT pass `jira_content_json`; the parts you staged "
        "with `qa_stage_jira` above are assembled server-side. If a "
        "staging call was rejected, fix that part and re-stage it "
        "before calling `qa_prepare_test_cases`."
    )


def _tail_rules() -> list[str]:
    """The verbatim-staging rules and the lead-in to the not-connected text."""
    return [
        "",
        "Stage each fetch VERBATIM. Do NOT summarise, reword, translate "
        "or truncate it, and do NOT strip fields - `fields.updated` in "
        "particular is how I tell whether the payload you sent is a fresh "
        "read or a cached copy, and I say so in the reply. If the result is "
        "too big to pass as `json` (over 20 KB), save the raw result to a "
        "file UNCHANGED and call `qa_stage_jira` with `path=<absolute file>` "
        "instead of `json`; I read it and trim bulky fields myself.",
        "",
        "RULES: qa_* MCP tools are the only path - never import handlers "
        "or spawn your own MCP client; never read tokens/keychains; "
        "generate NEW cases, never resubmit an old export; never edit or "
        "strip the staged ticket content.",
        "",
    ]


def _directive_tail(step: int, token: str) -> list[str]:
    """The closing step, the verbatim-staging rules and the not-connected text."""
    return [_tail_step(step, token), *_tail_rules()]
