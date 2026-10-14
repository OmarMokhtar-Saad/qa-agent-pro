"""Reply text of qa_push_suite (moved from handlers, C3a step 5).

The push is the only credentialed outbound write, so the refusal and the
"nothing was sent" wording are pinned byte for byte by
tests/test_submit_suite_push_characterization.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, NamedTuple

from tools.untrusted import single_line as _safe

PUSH_TARGETS = ("testrail", "xray")


@dataclass(frozen=True)
class PushRequest:
    """What one qa_push_suite call asks for, after normalisation."""

    suite_id: str
    target: str
    apply: bool
    project_id: Any = 0
    section_name: str = ""
    progress: Any = None


class PushFlag(NamedTuple):
    name: str
    enabled: bool


class PushView(NamedTuple):
    req: PushRequest
    flag: PushFlag
    total: int


def push_suite_refusal(target: str, apply: bool, flag: PushFlag) -> str:
    """The refusal text for an unknown target or a flag-OFF real push, else ""."""
    if target not in PUSH_TARGETS:
        return (
            "⚠️ Unknown push target "
            + repr(_safe(target, 40))
            + ". Choose one of: "
            + ", ".join(PUSH_TARGETS)
            + "."
        )
    if apply and not flag.enabled:
        return (
            "⚠️ **Nothing was sent.** A real push to "
            + target
            + " needs `"
            + flag.name
            + "=true` in `.env` and an MCP server restart. Re-run with `apply=false` "
            "for a preview of exactly what would be created."
        )
    return ""


def push_suite_reply(content: dict, view: PushView) -> str:
    """Format the preview or the pushed reply, with the batch-cap note."""
    req = view.req
    pushed = int(content.get("pushed") or 0)
    skipped = int(content.get("skipped") or 0)
    would = int(content.get("would_push") or 0)
    # The pushers cap the case list BEFORE counting, so `would_push`/`pushed` can
    # never reveal the truncation on their own -- compare against the suite.
    counted = would if not req.apply else pushed + skipped
    cap_note = push_cap_note(counted, view.total)
    if not req.apply:
        return push_preview_text(content, req, view.flag) + cap_note
    return push_done_text(content, req.target) + cap_note


def push_cap_note(counted: int, total: int) -> str:
    """The "pusher capped the batch" warning, or "" when nothing was cut."""
    if not (total and counted and counted < total):
        return ""
    return (
        "\n\n⚠️ Only the first "
        + str(counted)
        + " of "
        + str(total)
        + " cases were included — this pusher caps the batch. The rest were "
        "NOT sent."
    )


def _section_clause(content: dict) -> str:
    if not content.get("section_name"):
        return ""
    return " under section '" + _safe(content.get("section_name"), 60) + "'"


def push_preview_text(content: dict, req: PushRequest, flag: PushFlag) -> str:
    """The dry-run reply body (before the cap note)."""
    would = int(content.get("would_push") or 0)
    return (
        "## Push preview — "
        + req.target
        + "\n\n**Nothing was sent.** "
        + str(would)
        + " case(s) from suite `"
        + _safe(req.suite_id, 80)
        + "` would be created"
        + _section_clause(content)
        + ".\n\nRe-run with `apply=true` to push for real"
        + ("" if flag.enabled else " (needs `" + flag.name + "=true` first)")
        + "."
    )


def _created_line(content: dict) -> str:
    created = content.get("case_ids") or content.get("issue_keys") or []
    if not created:
        return ""
    return "- created: `" + _safe(", ".join(str(c) for c in created[:20]), 300) + "`\n"


def push_done_text(content: dict, target: str) -> str:
    """The real-push reply body (before the cap note)."""
    pushed = int(content.get("pushed") or 0)
    skipped = int(content.get("skipped") or 0)
    return (
        "✅ Pushed **"
        + str(pushed)
        + "** case(s) to "
        + target
        + (" (" + str(skipped) + " skipped)" if skipped else "")
        + ".\n\n"
        + _created_line(content)
        + "\nThis left your organisation and **nothing here deletes those cases — "
        "remove them in " + target + " if this was a mistake.**"
    )
