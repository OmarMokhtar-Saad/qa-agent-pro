"""Wizard test-case source menu: the source tables and the markdown fallback."""

from __future__ import annotations

from handlers import _common

# Sources offered by the wizard's "Generate test cases" branch — mirrors the
# Chainlit "Test cases" starter, which routes through the Feature Analysis
# wizard's Jira / Mobile / Jira + Mobile menu before generating the suite.
_TC_SOURCE_LABELS = {
    "Describe the feature": "describe",
    "From a Jira ticket": "jira",
    "From a web page URL": "web",
    "From a Swagger/OpenAPI link": "swagger",
    "From mobile screens": "mobile",
    "Jira + mobile screens": "jira_mobile",
}

_TC_SOURCE_PROMPTS = {
    "describe": "Describe the feature to test:",
    "jira": "Paste the Jira/issue URL (or describe the feature):",
    "jira_mobile": "Paste the Jira/issue URL (or describe the feature):",
    "web": "Paste the web page URL:",
    "swagger": "Paste the Swagger/OpenAPI spec URL:",
}

# The two screen-capture sources are the ONLY ones that route to a tool this
# server does not always register: both call `qa_feature_analysis` and then
# `qa_submit_feature_analysis`, which mcp_server registers inside
# `if not mcp_handlers.test_cases_only():`. The public distribution IS
# test-cases-only, so on it these two options told the client to call a tool
# that is not there, mid-flow, in front of a tester -- the identical silent
# failure mcp_server already guards for tools/guidance.py, which this menu
# escaped only because it lives here rather than in that module.
_TC_FULL_EDITION_SOURCES = ("mobile", "jira_mobile")

#: One menu line per source, keyed by the source id so the NUMBERS are derived
#: from the list actually emitted rather than written into the prose.
_TC_SOURCE_MENU_LINES = {
    "describe": "**Describe the feature** — user types it in plain language",
    "jira": "**Jira ticket** — user pastes the issue URL",
    "web": "**Web page** — user pastes the page URL (the live UI is read)",
    "swagger": "**Swagger/OpenAPI link** — user pastes the spec URL (API test cases)",
    "mobile": "**Mobile screens** — capture from a connected device",
    "jira_mobile": "**Jira + mobile screens** — merge the ticket with captured screens",
}

_TC_COUNT_WORDS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}


# The ``{label: source_id}`` map this EDITION can actually serve.
#
# The condition MIRRORS the registration site (``mcp_server``: ``if not
# mcp_handlers.test_cases_only():``): the edition check alone decides whether
# the pair exists.
#
# Note `qa_capture_screens` is registered on BOTH editions and is NOT what is
# gated here -- the missing tool is `qa_feature_analysis`, not device capture.
def _tc_sources() -> dict:
    if not _common.test_cases_only():
        return dict(_TC_SOURCE_LABELS)
    return {
        label: src
        for label, src in _TC_SOURCE_LABELS.items()
        if src not in _TC_FULL_EDITION_SOURCES
    }


_TC_MOBILE_MAPPING = (
    "; for `mobile` call `qa_feature_analysis` with `mode=mobile`; for "
    "`jira_mobile` call `qa_feature_analysis` with `mode=jira_mobile`. "
    "`mobile` and `jira_mobile` are a TWO-step chat-only flow: "
    "`qa_feature_analysis` hands YOU a task envelope to answer, then "
    "you call `qa_submit_feature_analysis` with its `task_id` and your "
    "JSON."
)


def _tc_mapping(srcs: list) -> str:
    typed = [s for s in srcs if s not in _TC_FULL_EDITION_SOURCES]
    mapping = (
        "After the user picks: for "
        + ", ".join(f"`{s}`" for s in typed)
        + " call `qa_generate_test_cases` with `feature_or_url` set to their "
        "text/URL"
    )
    if "mobile" in srcs:
        return mapping + _TC_MOBILE_MAPPING
    return mapping + "."


# Fallback when the native dialog is unavailable or was auto-dismissed.
#
# Written as an instruction to the HOST assistant: present the options THIS
# edition can serve as a structured multiple-choice question (editors render
# those reliably, unlike MCP elicitation dialogs), then call back with the
# answer.
#
# The count word and the mapping paragraph are DERIVED from ``_tc_sources()``.
# The text says "EXACTLY these N options" and "Do not invent different
# options", so N has to move with the list or the instruction contradicts
# itself.
#
# **The options are keyed, not numbered** (2026-09-04), for the reason
# ``tools/mobile/render.py``'s module docstring gives: the host re-presents
# them in its own question UI, which relabels (a/b/c/d) and reorders, so a
# position addressed the wrong option. These numbers never round-tripped as
# an argument -- the host is told to call a tool directly -- but the mapping
# paragraph named options by position, and a reordering UI would have sent
# the wrong one to `mode=mobile`.
def _tc_source_menu_markdown() -> str:
    srcs = list(_tc_sources().values())
    count = _TC_COUNT_WORDS.get(len(srcs), str(len(srcs)))
    body = "".join(f"- `{s}` \u2014 {_TC_SOURCE_MENU_LINES[s]}\n" for s in srcs)
    return (
        "## Ask the user: where is the feature coming from?\n\n"
        f"Present EXACTLY these {count} options to the user as a "
        "multiple-choice question (use your ask-user/questions UI, not prose), "
        "then follow the mapping below. Do not invent different options. Your "
        "UI may relabel or reorder them; the key on each line is what "
        "identifies it.\n\n" + body + "\n" + _tc_mapping(srcs)
    )
