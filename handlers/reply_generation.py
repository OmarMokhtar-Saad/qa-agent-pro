"""Generation and export replies (moved from handlers._reply)."""

from __future__ import annotations

import re
from typing import Any, NamedTuple

from handlers.reply_consts import _SUMMARY_CAP


class GenerationReply(NamedTuple):
    """The inputs of `shape_generation_result` (a parameter object)."""

    summary: str
    suite: Any
    suite_id: str
    status: str
    auto_export: bool = False
    submitted_count: int | None = None
    summary_cap: int = _SUMMARY_CAP


class ExportedFile(NamedTuple):
    """The inputs of `shape_export_result` (a parameter object)."""

    suite_id: str
    fmt: str
    path: str
    n_cases: int


# --------------------------------------------------------------------------- #
# Markdown shapers (pure, never-raise)
# --------------------------------------------------------------------------- #


def _cases_line(reply: GenerationReply, cases: int) -> str:
    """The "Cases" line, naming duplicates removed when the count differs."""
    sent = reply.submitted_count
    if sent is not None and sent != cases:
        return (
            f"**Cases:** {cases} "
            f"({sent} submitted, {sent - cases} removed "
            "as duplicates)"
        )
    return f"**Cases:** {cases}"


# With auto_export=True the caller appends a ready .xlsx path below, so
# nothing here points at qa_export_suite: the tester is handed the finished
# deliverable instead of a "which format?" question. The suite_id stays
# visible either way (it is still the handle for a different format).
#
# *summary_cap* (F03, 2026-08-16) is how much of *summary* fits once the
# caller knows what ELSE is in the reply. It defaults to the static
# _SUMMARY_CAP, so every direct caller -- including
# tests/test_finalize_reply_cap.py, which is the guard on that inner budget --
# is byte-identical. handle_submit_suite passes a smaller one when its
# disclosure notices need the room, because the summary is the block that
# yields: its content is the suite, and the suite is in the workbook.
def shape_generation_result(reply: GenerationReply) -> str:
    """Shape the generation reply."""
    suite = reply.suite
    cases = len(getattr(suite, "test_cases", []) or []) if suite is not None else 0
    icon = {"ok": "✅", "partial": "⚠️", "fallback": "⚠️", "error": "❌"}.get(
        reply.status, "ℹ️"
    )
    lines = [f"## {icon} Test cases generated", ""]
    if reply.suite_id:
        hint = "" if reply.auto_export else " — pass this to `qa_export_suite`."
        lines.append(f"**Suite ID:** `{reply.suite_id}`{hint}")
    lines.append(_cases_line(reply, cases))
    lines.append(
        f"**Status:** {_GENERATION_STATUS_TEXT.get(reply.status, reply.status)}"
    )
    body = (reply.summary or "").strip()
    if body:
        lines += ["", _truncate_summary(body, reply.summary_cap, reply.auto_export)]
    return "\n".join(lines)


# ops-5 (issue 8): `status` is an INTERNAL token and this reply goes to a
# non-technical tester. "fallback" in particular means "structured generation
# FAILED, this is markdown instead" -- it reads as benign, and a real run on
# 2026-07-29 reported exactly that with 0 cases. Translate it.
_GENERATION_STATUS_TEXT = {
    "ok": "Complete",
    "partial": "⚠️ Incomplete — some categories failed, cases are missing",
    "fallback": "❌ Generation failed — no structured test cases were produced",
    "error": "❌ Error — generation did not complete",
}


# What the marker may promise depends on WHAT was cut. The claim
# above is about CASES, and it is true of them. The advisory sections
# the summary also carries -- Suite Consistency, Test Data, the
# grounding notes -- reach the workbook only as the "Full generation
# advisory" row of its Generation Notes sheet, written when the
# summary is over _SUMMARY_CAP (submit_category_gen_notes). Without
# an auto-export there is no workbook, so the marker must not send
# the tester to look for them there.
def _truncation_tail(cut: str, auto_export: bool) -> str:
    """The marker's promise, worded for what was cut."""
    tail = (
        "the Excel file below has every case"
        if auto_export
        else "export the suite for the full set"
    )
    if "\n## " in cut or cut.lstrip().startswith("## "):
        tail += (
            ". Some advisory notes were cut from THIS reply -- read them "
            'in full on the workbook\'s "Generation Notes" sheet, which '
            "carries the whole advisory verbatim"
            if auto_export
            else ". Some advisory notes were cut and are NOT in the "
            "export -- re-submit a smaller suite to read them"
        )
    return tail


# K-12: the summary used to be cut by POSITION, so the last sections
# went first whatever they said. These headings are the low-value reading (a
# deliberate-tradeoff disclosure, per-case data hints); they leave first, whole,
# so the warnings above them are never the part that is cut.
_LOW_VALUE_HEADINGS = ("## Server-Side Review Steps Not Run", "## Test Data")


def _left_out_note(names: list[str]) -> str:
    """The note naming the dropped blocks and where (not) to read them."""
    return (
        f"\n\n> ℹ️  Left out to keep this reply readable: {', '.join(names)}. "
        "They are advisory text; a smaller suite shows the rest in full."
    )


_NOTE_ROOM = len(_left_out_note(["Server-Side Review Steps Not Run", "Test Data"]))


def _drop_low_value(body: str, summary_cap: int) -> tuple[str, str]:
    """(*body* minus low-value blocks dropped last-first, the Left-out note).

    The note's room is reserved before the fit test, so it is never the part cut.
    """
    blocks = re.split(r"(?=\n\n## )", body)
    names: list[str] = []
    for idx in range(len(blocks) - 1, -1, -1):
        if names and len(body) + _NOTE_ROOM <= summary_cap:
            break
        if blocks[idx].lstrip().startswith(_LOW_VALUE_HEADINGS):
            names.append(blocks[idx].strip().splitlines()[0].lstrip("# "))
            del blocks[idx]
            body = "".join(blocks)
    return body, (_left_out_note(names) if names else "")


def _truncate_summary(body: str, summary_cap: int, auto_export: bool) -> str:
    """*body* cut to *summary_cap* with an honest truncation marker."""
    if len(body) <= summary_cap:
        return body
    body, note = _drop_low_value(body, summary_cap)
    if len(body) + len(note) <= summary_cap:
        return body + note
    marker_len = len(_truncation_tail("\n## x", auto_export)) + 16
    room = max(0, summary_cap - marker_len - len(note))
    tail = _truncation_tail(body[room:], auto_export)
    out = body[:room].rstrip() + f"\n\n…(truncated — {tail})" + note
    return out[:summary_cap] if len(out) > summary_cap else out


def shape_export_result(item: ExportedFile) -> str:
    return (
        f"## ✅ Exported suite `{item.suite_id}` to {item.fmt}\n\n"
        f"**Cases:** {item.n_cases}\n"
        f"**File:** `{item.path}`"
    )


def shape_explore_step(session_id: str, sess: dict, step: str) -> str:
    mem = sess.get("memory") or {}
    covered = mem.get("covered_areas") or []
    turns = mem.get("turn_count", 0)
    return "\n".join(
        [
            f"## Exploratory coaching — session `{session_id}`",
            "",
            step.strip() or "(no step produced)",
            "",
            f"_Turn {turns} · covered areas: {', '.join(covered) if covered else 'none yet'}_",
            "",
            "Reply with what you found via `qa_explore_step` (same session_id) to continue.",
        ]
    )
