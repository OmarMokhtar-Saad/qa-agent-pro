"""The staged-categories view of a category reply: names, warning and banner."""

from __future__ import annotations

import logging

from agents.host_category_jobs import prep_status_view
from config.settings_guard import never_raise
from handlers import _reply
from handlers.submit_category_types import _StagedView

logger = logging.getLogger("tools.mcp_handlers")


def _status_view(expected: list, names: list) -> dict:
    """The prep status over the expected set: the one caller of prep_status_view."""
    return prep_status_view(expected=expected, staged_raw_names=names)


def _raw_names(rows: list) -> list:
    """The raw stored category name of every row that is a dict."""
    return [str(r.get("category_name") or "") for r in rows if isinstance(r, dict)]


def _staged_names(rows: dict) -> list:
    """The stripped, non-blank category names of the staged rows."""
    return [
        str(r.get("category_name", "")).strip()
        for r in (rows.get("content") or [])
        if isinstance(r, dict) and str(r.get("category_name", "")).strip()
    ]


def _unrecognised_warning(unrecognized: list) -> str:
    """The reply block that names the rows which do not count toward the set."""
    return _reply._unrecognized_names_line(
        unrecognized,
        prefix="> ⚠️  **Not a recognised category:** ",
        suffix=(
            ". Those rows are stored but will NOT be merged into "
            "the suite and do NOT count toward the staged set. Use "
            "a name from `orchestration.expected_categories` and "
            "re-submit those cases under it.\n\n"
        ),
    )


def _recognised_suffix(recognized: list) -> str:
    """The ` (a, b)` suffix naming the recognised staged categories, or nothing."""
    return " (" + ", ".join(recognized) + ")" if recognized else ""


# F4: name the categories already staged -- a host LLM re-reading this reply
# otherwise has only a bare count and cannot tell what is left.
#
# H1 (round-3 review, 2026-09-02). The suffix was
# `" (" + ", ".join(names) + ")"` -- the RAW row values, no cap and no fence
# handling, in a SUCCESS reply. The fourth occurrence of one class, and the one
# my own "the class is closed" commit missed, because its AST check only looked
# for a bare name inside an f-string and this is a `join`.
#
# Two routes were driven to it: an envelope carrying no `expected_categories`
# (which the code at the meta read calls a real state for an OLD envelope), and
# any exception in the block below -- whose `except` says "the count alone is
# still correct", while this raw value had already been assigned. Both produced
# a 1,701-byte reply with the fence intact and an injected `SYSTEM:` line
# standing as server prose.
#
# CANONICALISE first, which also settles the disagreement F11 was about:
# `qa_prep_status` renders the canonical `status["staged"]`, so this reply must
# not render something else. A name that does not normalise is still SHOWN --
# the tester needs to see what they typed -- but bounded and fence-safe, and the
# list is capped.
#
# F11 (2026-08-30): this reply counted every stored row, including a category
# name that normalises to nothing, and listed the bogus name among the staged
# categories -- while `qa_prep_status`, on the same prep a moment later,
# correctly reported `staged: 1/8` plus `unrecognized names: ...`. Two tools
# disagreeing about whether work was recorded is worse than either answer: the
# instructions tell a host to track progress from THESE replies (prep_status is
# optional), so it believed it was 2/8 done when it was 1/8. Same computation as
# prep_status, so they cannot drift again. Silent when the prep stamped no
# expected set (an old envelope), which keeps those replies byte-identical.


def _staged_category_view(rows: dict, meta: dict, on_file: int) -> _StagedView:
    """The staged count, name suffix and unrecognised-name warning for a reply."""
    staged_names = ""
    warning = ""
    with never_raise(logger, "could not list staged category names", exc_info=True):
        names = _staged_names(rows)
        if names:
            staged_names = _reply._staged_names_suffix(names)
        _expected = list((meta or {}).get("expected_categories") or [])
        if _expected:
            _view = _status_view(_expected, names)
            _recognized = list(_view.get("staged") or [])
            _unrecognized = list(_view.get("unrecognized") or [])
            if _unrecognized:
                on_file = len(_recognized)
                staged_names = _recognised_suffix(_recognized)
                warning = _unrecognised_warning(_unrecognized)
    return _StagedView(on_file, staged_names, warning)


def _banner_text(n: int) -> str:
    """The STOP banner body for a set of ``n`` expected categories."""
    return (
        f"## \U0001f3c1 All {n}/{n} expected categories are staged -- "
        "STOP generating\n\n"
        "**Do NOT regenerate, rewrite, or re-submit any category.** The set "
        "is complete; a re-submission only replaces near-identical rows and "
        "costs minutes of chat time. The single remaining step is to "
        "finalize: call `qa_submit_suite` with this prep_id now, using ONE "
        "of the routes described below.\n\n"
    )


# STOP banner once every expected category has a staged row.
#
# The 2026-08-03 22:17 live run (prep ea258c02) staged all 8 categories by
# 22:23 and then REGENERATED four of them anyway -- ~5 minutes and thousands
# of host-model tokens for zero net change, because no reply ever said the
# set was complete. Returns "" unless meta carries expected_categories and
# every one of them is staged. Advisory only; never raises.
def all_staged_banner(meta: object, rows_content: list) -> str:
    """STOP banner once every expected category has a staged row."""
    with never_raise(logger, "all-staged banner failed", exc_info=True):
        if not isinstance(meta, dict):
            return ""
        expected = [
            str(x) for x in (meta.get("expected_categories") or []) if str(x).strip()
        ]
        if not expected:
            return ""
        if not _status_view(expected, _raw_names(rows_content)).get("ready"):
            return ""
        return _banner_text(len(expected))
    return ""
