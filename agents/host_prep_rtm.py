"""Requirement-traceability (RTM) audit detail and the orphan-case note.

A live suite once carried 13 cases mapping to no acceptance criterion and was
accepted in silence, because the count only reached the audit DB. The note
renders it. Never a refusal: extra cases beyond the ACs are legitimate; the
failure being fixed is the SILENCE. tc_ids and titles are HOST-authored, so
they are sanitized before they reach a backtick span.
"""

from __future__ import annotations

import logging

from config.settings_guard import never_raise

logger = logging.getLogger("agents.host_mode")

_RTM_ORPHAN_MAX_NAMED = 5
_RTM_ORPHAN_TITLE_CAP = 80
_RTM_ORPHAN_ID_CAP = 16
_CASE_ID_ALLOWED = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)


def _rtm_trace(suite: object) -> dict | None:
    """The suite's traceability dict, or None when it carries none."""
    trace = getattr(suite, "_rtm_trace", None)
    if not isinstance(trace, dict) or not trace.get("acs"):
        return None
    return trace


def rtm_trace_detail(suite) -> dict:
    """The traceability counts, flattened for an audit detail dict ({} if none).

    Never raises -- the audit trail must not be able to break a generation.
    """
    with never_raise(logger, "could not read _rtm_trace", exc_info=True):
        trace = _rtm_trace(suite)
        if trace is None:
            return {}
        return {
            "rtm_acs": int(trace.get("acs", 0)),
            "rtm_covered": int(trace.get("covered", 0)),
            "rtm_orphan_cases": int(trace.get("orphan_cases", 0)),
        }
    return {}


def safe_case_id(raw: object) -> str:
    """Allowlist + cap ONE tc_id (a backtick or newline would break the span)."""
    # pragma: no cover - a sanitizer must never raise
    with never_raise():
        return "".join(ch for ch in str(raw or "") if ch in _CASE_ID_ALLOWED)[
            :_RTM_ORPHAN_ID_CAP
        ]
    return ""


def safe_case_title(raw: object) -> str:
    """Strip backticks and newlines from ONE host-authored title, then cap it."""
    # pragma: no cover - a sanitizer must never raise
    with never_raise():
        return (str(raw or "").replace("`", "").replace("\n", " ").strip())[
            :_RTM_ORPHAN_TITLE_CAP
        ]
    return ""


def _orphan_count(suite: object) -> int:
    """How many cases trace to no AC (0 when there is no usable trace)."""
    trace = _rtm_trace(suite)
    if trace is None:
        return 0
    try:
        return int(trace.get("orphan_cases", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _orphan_text(orphans: int, total: int, first: str) -> str:
    return (
        "> ℹ️  **Requirement mapping: "
        f"{orphans} of {total} case(s) trace to NO acceptance criterion.**"
        + first
        + " Either set each one's `requirement_id` to the AC it verifies, or "
        "confirm they are deliberate additions -- extra cases are perfectly "
        "legitimate, but an untraced case is invisible to the requirement "
        "coverage report, so nobody can tell the two apart. Nothing was "
        "changed or dropped.\n\n"
    )


def rtm_orphan_note(suite: object) -> str:
    """Submit-time disclosure of cases tracing to NO acceptance criterion.

    "" when there is no trace data, no ACs or no orphans (byte-identical reply
    without the data). Names the first few ids with sanitized titles. NOTHING is
    dropped, rewritten or refused. Never raises.
    """
    with never_raise(logger, "_rtm_orphan_note failed", exc_info=True):
        orphans = _orphan_count(suite)
        if orphans <= 0:
            return ""
        cases = list(getattr(suite, "test_cases", None) or [])
        named, more = rtm_orphan_named(suite, cases, orphans)
        first = f" First: {named}{more}." if named else ""
        return _orphan_text(orphans, len(cases), first)
    return ""


def _orphan_ids(suite: object) -> list:
    raw = getattr(suite, "_rtm_orphan_ids", None) or []
    ids = [i for i in (safe_case_id(x) for x in raw) if i]
    return ids[:_RTM_ORPHAN_MAX_NAMED]


def _case_titles(cases: list) -> dict:
    titles: dict = {}
    for tc in cases:
        # pragma: no cover - defensive
        with never_raise() as g:
            titles[safe_case_id(getattr(tc, "tc_id", ""))] = safe_case_title(
                getattr(tc, "title", "")
            )
        if g.failed:
            continue
    return titles


def rtm_orphan_named(suite: object, cases: list, orphans: int) -> tuple[str, str]:
    """(the named first orphans, the "(+N more)" marker) for the orphan note."""
    ids = _orphan_ids(suite)
    titles = _case_titles(cases)
    named = ", ".join(
        (f'`{i}` "{titles[i]}"' if titles.get(i) else f"`{i}`") for i in ids
    )
    more = f" (+{orphans - len(ids)} more)" if orphans > len(ids) else ""
    return named, more
