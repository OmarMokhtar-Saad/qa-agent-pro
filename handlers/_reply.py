"""Moved from handlers (refactor P2): _reply."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import NamedTuple

from config.settings import settings
from handlers import _common
from tools.untrusted import wrap_untrusted

logger = logging.getLogger("tools.mcp_handlers")

_SUMMARY_CAP = 4000  # cap the embedded generation summary in the shaped result


# --------------------------------------------------------------------------- #
# Markdown shapers (pure, never-raise)
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Finalize-reply budget (F03, 2026-08-16)
# --------------------------------------------------------------------------- #
# _SUMMARY_CAP above bounds ONE of the twenty slots the submit reply is
# assembled from -- the `summary` embedded by shape_generation_result. The
# 2026-08-16 live run measured 4844 chars, of which 2006 (41%) sat in the notice
# preamble that no cap has ever seen: the notes are concatenated one level up,
# at the return of handle_submit_suite, and that string is what the tester
# reads.
#
# WHAT YIELDS. Almost every note in that preamble is a disclosure with its own
# written "must always be surfaced" contract -- dropped_note (:5390), the
# always-on coverage signal (:5346), the image-relevance warning the submit
# AUDIT row claims was shown (:8095), the duplicate note that reports cases the
# server actually REMOVED (:7842). Trimming those to make room for prose would
# be a worse bug than an over-long reply, and one of them would additionally
# make the audit trail assert a disclosure that never happened. So they are
# PROTECTED, and the block that yields is `summary`: its content is the suite,
# the suite is in the workbook, and its truncation marker already says so.
# Only when the summary has yielded to its floor do four genuinely repeatable
# sections drop -- and they are NAMED where they were, so nothing is silent.
_REPLY_CAP = 6000

# The summary never shrinks below this. A reply with no summary at all is not a
# cheaper reply, it is a different (and useless) one.
_SUMMARY_FLOOR = 1200

# What shape_generation_result appends AFTER the slice when it truncates -- a
# blank line and the "…(truncated — …)" marker. It is charged to the reply but
# is not part of `summary`, so a budget that ignores it over-runs the cap by
# exactly this much on every truncated reply. Reserved unconditionally rather
# than only when the summary is actually cut, because the budget is computed
# BEFORE the shaping that would decide.
_TRUNC_RESERVE = 64

# Drop priorities for the four trimmable sections. LOWER drops FIRST, and each
# constant names WHY that section is droppable at all -- a section that cannot
# answer that question is protected instead.
_REPLY_P_EXPORTED = 1  # the same disclosure is written into the export
_REPLY_P_POINTER = 2  # names an on-demand tool that reproduces it
_REPLY_P_PROVENANCE = 3  # how a field was derived, not what it says
_REPLY_P_REPORT = 4  # a measurement section, re-derivable from the suite


class ReplySection(NamedTuple):
    """One slot of the submit reply. *text* carries its own separators.

    `protected` is not a priority: a protected section is never dropped and
    never truncated, whatever the budget says.
    """

    name: str
    text: str
    priority: int = _REPLY_P_REPORT
    protected: bool = False


def _omission_marker(names: list[str]) -> str:
    """Disclose what the budget left out, BY NAME.

    The wording is deliberately narrow. An earlier draft said "the Excel file
    and the Suite ID cover every case", which is false for three of the notes
    this reply can carry -- the duplicate note reports cases the server removed,
    and neither it nor the dropped-case note nor the image warning is written
    into the workbook. Only sections that genuinely repeat something the tester
    can still reach are droppable, and this text claims exactly that and no
    more.
    """
    return (
        "\n\n> \u2139\ufe0f  "
        f"{len(names)} informational section(s) were left out to keep this "
        f"reply readable: {', '.join(names)}. Every notice about whether this "
        "suite is VALID is still above -- what was left out repeats something "
        "you can still reach (the export, the coverage notes, or a tool you "
        "can call on demand).\n"
    )


def summary_budget(sections: list[ReplySection], header_len: int) -> int:
    """How many chars of `summary` fit, given everything else in the reply.

    *sections* must be EVERY section of the reply except the one the summary
    sits in -- including the ones appended after it, which the round-2 review
    caught this missing: `cap_note` is added at the assemble call and was never
    counted, so the reply over-ran the cap by its length. Pass the whole list;
    the only thing left out is the suite block.

    Clamped into [_SUMMARY_FLOOR, _SUMMARY_CAP], so this can only ever be
    STRICTER than the cap that shipped, never looser: a reply with few notes
    keeps today's exact 4000-char allowance and stays byte-identical.
    """
    try:
        overhead = (
            sum(len(s.text) for s in sections) + max(header_len, 0) + _TRUNC_RESERVE
        )
        return max(_SUMMARY_FLOOR, min(_SUMMARY_CAP, _REPLY_CAP - overhead))
    except Exception:  # pragma: no cover - defensive
        logger.debug("summary budget failed - using the static cap", exc_info=True)
        return _SUMMARY_CAP


def assemble_finalize_reply(sections: list[ReplySection], cap: int = _REPLY_CAP) -> str:
    """Join *sections* into the submit reply, bounded by *cap*. Never raises.

    UNDER budget the result is byte-identical to ``"".join(s.text ...)``. That
    fast path is the point of the design: every healthy run keeps today's exact
    reply, so every assertion pinned on it stays true.

    OVER budget, trimmable sections are dropped WHOLE and in STRICT priority
    order -- the loop stops at the first section it can keep, so a long
    high-priority section can never lose its place to a short low-priority one.
    The names of the dropped sections are disclosed in a trailing marker whose
    length is reserved BEFORE the decision, because disclosure may not be the
    thing that pushes the reply back over the bound.

    A protected section is never dropped and never truncated, so the bound is
    honest rather than absolute: if the protected band alone exceeds *cap*, the
    reply is that band plus the marker. Bookkeeping is keyed on INDEX, not on
    name, so a future section that reuses a name cannot silently duplicate or
    delete another section's text.
    """
    try:
        joined = "".join(s.text for s in sections)
        if len(joined) <= cap:
            return joined
        dropped = _sections_to_drop(sections, len(joined), cap)
        if not dropped:
            return joined
        gone = set(dropped)
        body = "".join(s.text for i, s in enumerate(sections) if i not in gone)
        out = body + _omission_marker([sections[i].name for i in sorted(gone)])
        # Dropping may never make the reply LONGER than leaving it alone. The
        # marker costs ~300 chars, so a droppable set smaller than that is a net
        # loss -- and the tester would have paid a lost section for nothing.
        # Round-2 review, the tail of MAJOR 1.
        return out if len(out) < len(joined) else joined
    except Exception:
        # Fail OPEN, and defensively: the recovery may not re-run whatever threw
        # (a genexpr over the same texts is exactly what threw above), or the
        # tester loses the entire reply to a bounding bug.
        logger.warning(
            "finalize reply budget failed - returning the reply unbounded",
            exc_info=True,
        )
        return _unbounded_reply(sections)


def _sections_to_drop(sections: list[ReplySection], used: int, cap: int) -> list[int]:
    """Indexes of the trimmable sections to drop, cheapest-to-lose first."""
    trimmable = [(i, s) for i, s in enumerate(sections) if not s.protected and s.text]
    dropped: list[int] = []
    # STRICT order: cheapest-to-lose first, and every one of them goes until
    # the reply fits. No greedy back-fill -- see the docstring.
    #
    # The reserve is what the marker would cost IF THE LOOP STOPPED HERE, so
    # it is recomputed from the names dropped so far. Reserving for every
    # trimmable name up front (the round-3 review, MINOR 4) over-charged a
    # borderline reply and could drop one more section than it needed to --
    # the tester paying a section for a disclosure that was never emitted.
    for idx, sec in sorted(trimmable, key=lambda pair: (pair[1].priority, pair[0])):
        reserve = (
            len(_omission_marker([sections[i].name for i in dropped])) if dropped else 0
        )
        if used + reserve <= cap:
            break
        used -= len(sec.text)
        dropped.append(idx)
    return dropped


def _unbounded_reply(sections: list[ReplySection]) -> str:
    """Fail-open join: tolerates malformed sections, never raises."""
    parts: list[str] = []
    for sec in sections or []:
        try:
            parts.append(str(getattr(sec, "text", "") or ""))
        except Exception:  # pragma: no cover - defence in depth
            continue
    return "".join(parts)


def shape_generation_result(
    summary: str,
    suite,
    suite_id: str,
    status: str,
    *,
    auto_export: bool = False,
    submitted_count: int | None = None,
    summary_cap: int = _SUMMARY_CAP,
) -> str:
    """Shape the generation reply.

    With auto_export=True the caller appends a ready .xlsx path below, so
    nothing here points at qa_export_suite: the tester is handed the finished
    deliverable instead of a "which format?" question. The suite_id stays
    visible either way (it is still the handle for a different format).

    *summary_cap* (F03, 2026-08-16) is how much of *summary* fits once the
    caller knows what ELSE is in the reply. It defaults to the static
    _SUMMARY_CAP, so every direct caller -- including
    tests/test_finalize_reply_cap.py, which is the guard on that inner budget --
    is byte-identical. handle_submit_suite passes a smaller one when its
    disclosure notices need the room, because the summary is the block that
    yields: its content is the suite, and the suite is in the workbook.
    """
    cases = len(getattr(suite, "test_cases", []) or []) if suite is not None else 0
    icon = {"ok": "✅", "partial": "⚠️", "fallback": "⚠️", "error": "❌"}.get(status, "ℹ️")
    lines = [f"## {icon} Test cases generated", ""]
    if suite_id:
        hint = "" if auto_export else " — pass this to `qa_export_suite`."
        lines.append(f"**Suite ID:** `{suite_id}`{hint}")
    if submitted_count is not None and submitted_count != cases:
        lines.append(
            f"**Cases:** {cases} "
            f"({submitted_count} submitted, {submitted_count - cases} removed "
            "as duplicates)"
        )
    else:
        lines.append(f"**Cases:** {cases}")
    lines.append(f"**Status:** {_GENERATION_STATUS_TEXT.get(status, status)}")
    body = (summary or "").strip()
    if body:
        lines += ["", _truncate_summary(body, summary_cap, auto_export)]
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


def _truncate_summary(body: str, summary_cap: int, auto_export: bool) -> str:
    """*body* cut to *summary_cap* with an honest truncation marker."""
    if len(body) <= summary_cap:
        return body
    tail = (
        "the Excel file below has every case"
        if auto_export
        else "export the suite for the full set"
    )
    # What the marker may promise depends on WHAT was cut. The claim
    # above is about CASES, and it is true of them. It is false of the
    # advisory sections the summary also carries -- Suite Consistency,
    # Test Data, the grounding notes -- because none of those is written
    # into the workbook: the export carries the cases and (since F06)
    # their requirement ids, and no advisory at all. F14 measured the
    # Suite Consistency block alone at 4173 bytes against a 4000
    # _SUMMARY_CAP, so a squeeze reaching an advisory heading is
    # reachable, not theoretical. Telling that tester the export has it
    # sends them to look for something that is not there.
    cut = body[summary_cap:]
    if "\n## " in cut or cut.lstrip().startswith("## "):
        tail += (
            ". Some advisory notes were cut from THIS reply -- read them "
            'in full on the workbook\'s "Generation Notes" sheet, which '
            "carries the whole advisory verbatim"
            if auto_export
            else ". Some advisory notes were cut and are NOT in the "
            "export -- re-submit a smaller suite to read them"
        )
    return body[:summary_cap].rstrip() + f"\n\n…(truncated — {tail})"


def shape_export_result(suite_id: str, fmt: str, path: str, count: int) -> str:
    return (
        f"## ✅ Exported suite `{suite_id}` to {fmt}\n\n"
        f"**Cases:** {count}\n"
        f"**File:** `{path}`"
    )


# How many unrecognized category names a reply lists before summarising the
# rest. The point of the line is to show the tester which name was not
# recognised; a hundred of them tells them nothing and crowds out the
# staged/missing lines they came for.
_UNREC_SHOWN = 8


# Distinguishes "this exit declared no recovery target" from "this exit forgot
# to declare one". See the invariant at the `kind == "error"` exit of
# `handle_prepare_api_tests`.
_NO_RECOVERY_DECLARED = object()


def _staged_names_suffix(names: object) -> str:
    """`" (A, B, C)"` for the staged-category names, or `""`.

    Canonical where a name normalises, bounded and fence-safe where it does not,
    and capped either way. Shares `_UNREC_SHOWN` and `_clip_echo` with
    `_unrecognized_names_line` so the two cannot drift -- drift between two
    renderers of the same values is precisely what round 3 found. Never raises;
    the caller renders the COUNT separately, so an empty return still leaves a
    correct reply.
    """
    try:
        from agents.host_category_jobs import normalize_category

        out: list[str] = []
        for raw in names or []:
            text = str(raw or "").strip()
            if not text:
                continue
            try:
                canon = normalize_category(text)
            except Exception:  # a canonicaliser must not break a reply
                canon = ""
            out.append(canon or _clip_echo(text, 60))
        if not out:
            return ""
        shown = out[:_UNREC_SHOWN]
        rest = len(out) - len(shown)
        return " (" + ", ".join(shown) + (f", +{rest} more" if rest > 0 else "") + ")"
    except Exception:
        logger.debug("could not render staged category names", exc_info=True)
        return ""


def _unrecognized_names_line(names: object, *, prefix: str, suffix: str) -> str:
    """The ONE rendering of ``prep_status_view()["unrecognized"]``.

    THE INVARIANT (2026-09-02, from the review of 05756f79):

        a host-supplied name reaches a reply only through a bounded, fence-safe
        renderer -- and there is exactly ONE renderer, because two readers of
        the same list is how the first one got fixed and the second did not.

    That commit bounded `qa_prep_status` and claimed it was "the ONE echo site
    F6's sweep never visited". It was wrong by one: `qa_submit_category` read
    the SAME list 350 lines away through a hand-written `str(n)[:60]`, with no
    fence handling and no count cap, and an executing review demonstrated the
    fence, the newlines and an injected `SYSTEM:` line surviving into its
    success reply with all 52 names listed.

    Patching that second site would have left the class open for the third. The
    list now has one renderer and a test asserts nothing else formats it.

    Never raises.
    """
    try:
        # Round-3 review, L2: this said `except TypeError` and the docstring
        # said "Never raises". Both were narrower than the truth -- an
        # `__iter__` raising anything else, or a member whose `__str__` raises,
        # went straight through, because `str(n or "")` runs BEFORE
        # `_clip_echo`, which is the part that carries a guard. Both callers sit
        # inside a broad `except Exception`, so it was contained; a never-raise
        # docstring that is not true is still worth more than the four lines it
        # costs to make it true.
        items = [n for n in (names or []) if str(n or "").strip()]
    except Exception:
        logger.debug("could not read the unrecognized name list", exc_info=True)
        return ""
    if not items:
        return ""
    shown = [_clip_echo(n, 60) for n in items[:_UNREC_SHOWN]]
    rest = len(items) - len(shown)
    return (
        prefix
        + ", ".join(f"`{s}`" for s in shown)
        + (f" (+{rest} more)" if rest > 0 else "")
        + suffix
    )


def _clip_echo(value: object, limit: int = 120) -> str:
    """A caller-supplied identifier, bounded so it is safe to render back.

    F20 (2026-09-02 audit): the error replies for a rejected `format`,
    `prep_id`, `base_url`, `suite_id` or `category_name` echoed the value
    VERBATIM, so a 300 KB argument produced a 300 KB tool reply -- the whole of
    it into the tester's chat context, describing a value the server had
    already refused. Never raises.

    2026-09-02, review of the merged branch: this bounded LENGTH only, while its
    twin ``tools.untrusted.single_line`` (added by the docs workstream on this
    same branch, and imported into this module) also strips the code fences and
    newlines a value needs to escape the span it is rendered into. Every caller
    here renders the result inside a backtick span in server-authored prose the
    host model reads as TRUSTED, so a crafted `prep_id` could close its own span
    and continue as server text. Two helpers for one job is how that half went
    missing; the fence-safety now comes from the one helper that has it, and the
    truncation note this function adds is kept because it tells the tester the
    value was long rather than wrong.
    """
    from tools.untrusted import single_line

    try:
        text = single_line(value, limit=limit)
    except Exception:  # pragma: no cover - a hostile __str__
        return "<unprintable>"
    try:
        raw_len = len(str(value or ""))
    except Exception:  # pragma: no cover - a hostile __str__
        return text
    if raw_len <= limit:
        return text
    return text.rstrip("\u2026") + f"… (truncated, {raw_len} chars)"


def _submission_bytes(obj: object) -> int:
    """The size of a host submission AS ``parse_host_suite`` measures it.

    Mirrors that function's two branches EXACTLY, which is the whole point:
        * a str  -> ``len(obj.encode("utf-8", "ignore"))``
        * a dict -> ``len(json.dumps(obj, ensure_ascii=False).encode(...))``

    A single ``json.dumps`` for both shapes is what the first attempt at the
    Path-B bound did, and it RE-ESCAPED an already-serialised submission: a
    57,129-byte payload measured 64,297, so the refusal's number was wrong and
    the effective cap ~11% tighter than the configured one. The invariant that
    came out of it -- measure a size bound in the representation the cap is
    denominated in -- is why this helper exists rather than another caller of
    the sidecar one. Returns 0 for anything unmeasurable; never raises.
    """
    try:
        if isinstance(obj, str):
            return len(obj.encode("utf-8", "ignore"))
        if isinstance(obj, dict):
            return len(json.dumps(obj, ensure_ascii=False).encode("utf-8", "ignore"))
    except (TypeError, ValueError, RecursionError):
        return 0
    return 0


def _oversized_full_suite_note(obj: object) -> str:
    """``""`` if a FULL (Path-B) submission is within the cap, else a refusal.

    Deliberately separate from ``_oversized_submission_note``: that one speaks
    about "the review fields in this submission", which is false here -- this
    object is the whole suite, test cases included -- and the second invariant
    from the reverted attempt is that a refusal may only assert facts its own
    branch knows. Never raises.
    """
    cap = int(getattr(settings, "qa_prep_max_bytes", 0) or 0)
    if not cap or obj is None:
        return ""
    # `_submission_bytes` returns 0 for anything it cannot measure, and 0 is
    # under every positive cap, so an unmeasurable submission passes here.
    #
    # CORRECTED 2026-09-02 (review of this commit): this used to add "and is
    # refused by name a few lines later in `parse_host_suite`". That is false on
    # the branch that calls this helper -- Path B passes
    # `enforce_size_cap=False`, and `parse_host_suite`'s
    # "not JSON-serialisable" refusal lives INSIDE its `if cap and
    # enforce_size_cap` block, so it is skipped. The reviewer demonstrated it by
    # running the same dict both ways. The consequence is bounded and stated at
    # that call site: MCP tool arguments arrive already JSON-decoded, so only an
    # in-process caller can deliver an unmeasurable object, and no BOUND is lost
    # because every JSON-expressible shape measures exactly.
    #
    # There is deliberately no `not size` clause: it could never decide
    # anything, and a mutation proved it.
    size = _submission_bytes(obj)
    if size <= cap:
        return ""
    return (
        f"\u26a0\ufe0f That suite is {size} bytes, over the {cap}-byte limit for a "
        "single submission. Nothing was discarded. Send it as per-category "
        "submissions with `qa_submit_category` (each category is measured on its "
        "own), then finalize with `qa_submit_suite` and the same prep_id -- the "
        "server's own merge of accepted categories is NOT subject to this limit."
    )


def _suite_unreadable_reply(suite_id: str, loaded: object) -> str:
    """ "No such suite" and "the suite is there, every row failed" -- said apart.

    F21 (2026-09-02 audit): both arrive from the store as ``content=None``, and
    the single reply that covered them told a tester whose stored rows had all
    failed validation to "generate one first". That is false, it hides the real
    failure, and it invites a full regeneration of cases that were never lost.
    Never raises.
    """
    ident = _clip_echo(suite_id, 80)
    try:
        exists = bool((loaded or {}).get("exists"))
        total = int((loaded or {}).get("row_total") or 0)
    except Exception:  # pragma: no cover - defensive
        exists, total = False, 0
    if exists and total:
        return (
            f"⚠️ Suite `{ident}` EXISTS, but {total} of its {total} stored "
            "case(s) could NOT be read back -- every row failed validation, so "
            "there is nothing to export.\n\n"
            "**Nothing was deleted; the rows are still in the store.** This "
            "normally means they were written by a different build of the "
            "server. The per-row reason is in the server log. Do not "
            "regenerate before checking it -- run `qa-doctor` first."
        )
    if exists:
        return (
            f"⚠️ Suite `{ident}` EXISTS but has no stored cases, so there is "
            "nothing to export."
        )
    return f"⚠️ No stored suite with id `{ident}`. Generate one first."


#: What a device row that is not a dict becomes. It is NOT dropped: the audit
#: count and the rendered listing both follow the row count, so a silent drop
#: makes both under-report what `device_manager` produced.
_UNREADABLE_ROW = {
    "id": "",
    "name": "unreadable device row",
    "platform": "",
    "kind": "",
}


# Design notes for `_android_device_details` (kept as a comment so the function
# itself stays within the 50-line limit):
#
# A tester asked which emulator was running and could not be told: the row
# said `sdk gphone64 arm64` and nothing else -- no AVD name, no API level
# (2026-09-08).
#
# NO NEW PRODUCER. ``tools.mobile.emulator.avd_name_of`` stays the one
# producer of an AVD name and ``tools.mobile.adb.device_facts`` the one
# producer of the API level; nothing is re-derived here, because two
# derivations of one fact drift.
#
# THE STATED ELSE: a build with no ``tools/mobile`` on disk (the
# test-cases-only edition -- ``_mobile_modules_present`` is this file's single
# predicate for that) gets its rows EXACTLY as before, rather than a second,
# duplicate `adb` probe written here. A probe that fails, times out or finds
# nothing also leaves its row untouched: ``device_manager`` never raises, and
# this must not be the thing that changes that. Each probe is bounded by
# ``_MOBILE_PROBE_S`` for the reason ``_mobile_doctor_section`` states -- a
# device probe that hangs turns a listing into the symptom.
#
# Returns COPIES, so no caller's dicts are mutated under it, and the added
# keys are purely additive for every other reader of a device row.
#
# ONE ROW OUT PER ROW IN. A row this server cannot read used to be DROPPED,
# and `handle_list_devices` audits `len(devices)` -- so the count followed the
# drop and the listing under-reported what `device_manager` produced, with no
# trace. It is normalised instead, and rendered as unreadable.
#
# THE BUDGET GATES STARTING WORK, NEVER THE WORK ALREADY STARTED. Each probe
# was bounded and the LISTING was not: five hanging devices cost five budgets
# inside a single MCP call (40s measured). A device that is ADMITTED keeps
# both of its own full `_MOBILE_PROBE_S` probes, so a single slow-but-working
# device answers exactly as it does today -- sharing one budget ACROSS a
# row's two probes would time the second one out where it succeeds now, which
# is a regression this bound is not worth. What is bounded is the listing:
# once the budget is spent no further device is probed, so the worst case is
# one budget plus ONE ROW'S TWO PROBES whatever the device count -- three
# times ``_MOBILE_PROBE_S``, because the gate is checked BEFORE a row and a
# row admitted just inside the budget still runs both of its own probes. This
# said "one budget plus one probe", i.e. 16s; measured, it is 23.9s. A
# documented bound that measurement contradicts is a false claim, and a
# reader sizing a client timeout off it would size it short. A device reached
# after that keeps its row exactly as it arrived -- the same degradation a
# failed or timed-out probe already had.
async def _android_device_details(devices: list) -> list:
    """*devices* with ``avd`` / ``api`` filled in for Android rows. Never raises.

    Rationale and the probe-budget bound: the comment block above."""
    rows = [
        dict(dev) if isinstance(dev, dict) else dict(_UNREADABLE_ROW)
        for dev in (devices or [])
    ]
    if not rows or not _common._mobile_modules_present():
        return rows
    try:
        from tools.mobile import adb as mobile_adb
        from tools.mobile import emulator as mobile_emulator
    except Exception:  # pragma: no cover - defensive
        logger.debug("mobile modules unavailable for device detail", exc_info=True)
        return rows
    deadline = time.monotonic() + _MOBILE_PROBE_S
    for row in rows:
        serial = str(row.get("id") or "")
        if not serial or str(row.get("platform") or "") != "android":
            continue
        # The one gate, and it is per ROW: see the docstring. An admitted row
        # keeps its probes' own bounds; a listing whose budget is gone stops
        # asking.
        if time.monotonic() >= deadline:
            continue
        try:
            if str(row.get("kind") or "") == "emulator":
                named = await asyncio.wait_for(
                    mobile_emulator.avd_name_of(serial), timeout=_MOBILE_PROBE_S
                )
                row["avd"] = str((named or {}).get("content") or "")[:60]
            facts = await asyncio.wait_for(
                mobile_adb.device_facts(serial), timeout=_MOBILE_PROBE_S
            )
            row["api"] = str(((facts or {}).get("content") or {}).get("api") or "")[:8]
        except Exception:
            logger.info("device detail probe failed for one device", exc_info=True)
    return rows


def shape_devices(
    devices: list,
    unusable: list | None = None,
    adb_dropped_lines: int = 0,
    *,
    booting: list | None = None,
    empty_hint: str = "",
    adb_problem: str = "",
) -> str:
    """The device list a tester reads. *unusable* is rendered SEPARATELY.

    WHY A SEPARATE SECTION rather than a row in the list above it: this list is
    also the PICK LIST -- `_device_menu_markdown` renders these very rows as the
    tester's choices. An `unauthorized` phone offered as a choice fails opaquely
    inside a capture or a mobile run, and would push the non-`device` case into
    four more consumers whose right answer is "ignore". A separate section is
    informational by construction: impossible to select, impossible to miss.

    And the empty text is conditional on BOTH lists, which is the whole defect:
    a tester must never read "No devices detected" while a phone is plugged in.
    """
    attached = list(unusable or [])
    notice = _adb_notice(adb_dropped_lines, adb_problem)
    if not devices and not attached:
        return _no_devices_reply(booting, empty_hint) + notice
    if not devices:
        lines = ["## Devices (0)", "", "Nothing is usable yet — see below."]
    else:
        lines = [f"## Devices ({len(devices)})", ""]
    lines.extend(_device_row_line(dev) for dev in devices)
    if attached:
        lines.extend(_attached_devices_lines(attached))
    return "\n".join(lines) + notice


def _adb_notice(adb_dropped_lines: int, adb_problem: str) -> str:
    """The adb warnings appended to every device reply ("" when there are none)."""
    try:
        dropped = max(0, int(adb_dropped_lines or 0))
    except (TypeError, ValueError, OverflowError):
        # OverflowError is not decoration: `int(float("inf"))` raises it, this
        # function promises never to raise, and the value arrives from a dict
        # a caller assembled. Falling to 0 means the notice is omitted rather
        # than the whole device list being lost to a traceback.
        dropped = 0
    # OUR PROSE, and deliberately OUTSIDE the untrusted block below, exactly
    # like the remediation text: this is the sentence the tester acts on and no
    # device may dress its own words as it. The parameter has a default only
    # because a one-argument call is pinned; both production call sites pass it
    # explicitly and a test asserts they do, so the default cannot silence it.
    notice = (
        (
            "\n\n> \u26a0\ufe0f adb listed more transports than this server "
            "reads, so " + str(dropped) + " line(s) were not parsed. A device "
            "missing from this list may be one of them \u2014 unplug what you "
            "are not using, or run `adb kill-server`, then retry "
            "`qa_list_devices`."
        )
        if dropped
        else ""
    )
    if adb_problem:
        # OUR sentence about adb (B4): a timed-out `adb start-server` or
        # `adb devices -l` is otherwise indistinguishable from "no devices".
        notice += "\n\n> \u26a0\ufe0f " + " ".join(str(adb_problem).split())[:400]
    return notice


def _no_devices_reply(booting: list | None, empty_hint: str) -> str:
    """The reply for "nothing listed at all", without the adb notice."""
    # The notice belongs on THIS branch most of all: 200+ unparseable lines
    # yield zero rows, and "No devices detected" while a phone is plugged
    # in is the very defect this section exists to prevent.
    # BOOTING, not absent (fix round 3, item 5): `booting` is
    # `emulator.recently_started()`, passed only when both lists are
    # empty. The heading is `render.starting_heading`, the sentence
    # `qa_mobile_test` already says for the same state.
    if booting:
        from tools.mobile import render as mobile_render

        block = mobile_render.booting_devices_block(booting)
        if block:
            return block
    return (
        "No devices detected. Connect an Android device/emulator or boot an "
        "iOS simulator, then retry `qa_list_devices`."
        + ("\n\n" + str(empty_hint) if empty_hint else "")
    )


def _device_row_line(dev: dict) -> str:
    """One bullet of the device pick list."""
    row = (
        f"- `{dev.get('id')}` — {dev.get('name')} "
        f"({dev.get('platform')}/{dev.get('kind')})"
    )
    # Printed only when a producer answered. An absent AVD name or API
    # level renders the row exactly as it did before, which is what makes
    # the enrichment safe to fail.
    detail = ", ".join(
        part
        for part in (
            ("AVD `" + str(dev.get("avd"))[:60] + "`") if dev.get("avd") else "",
            ("API " + str(dev.get("api"))[:8]) if dev.get("api") else "",
        )
        if part
    )
    return row + (" — " + detail if detail else "")


def _attached_devices_lines(attached: list) -> list[str]:
    """The "Detected but not usable yet" section, one entry per output line."""
    lines = [
        "",
        f"## Detected but not usable yet ({len(attached)})",
        "",
        "These are plugged in, but this server cannot drive them until "
        "the step below is done — so they are not offered as a choice.",
        "",
    ]
    reported: list[str] = []
    steps: list[str] = []
    for dev in attached:
        serial = str(dev.get("id") or "?")[:80]
        state = str(dev.get("raw_state") or dev.get("state") or "unknown")[:80]
        reported.append(serial + " — adb reports: " + state)
        fix = str(dev.get("remediation") or "").strip()
        if fix and fix not in steps:
            steps.append(fix)
    # THE DEVICE'S OWN WORDS, WRAPPED. The serial and the state token are
    # read off a USB device this server does not control, and this reply
    # goes straight to the tester's chat model, so they are
    # externally-sourced text under the same hard rule as fetched URLs and
    # Jira content. No exemption was available: the bounded state
    # vocabulary constrains `state`, NOT `raw_state` on the unknown branch
    # and not `-l` metadata, so escaping alone is not the standard here.
    # Wrapping also strips a device that tries to close the block itself --
    # the raw-state cap is wider than the closing tag.
    lines.append(
        wrap_untrusted("attached_devices_adb", "\n".join(reported), limit=2000)
    )
    # OUR text, deliberately OUTSIDE the block and de-duplicated: this is
    # what the tester acts on, and a device must never be able to dress its
    # own words as the instruction.
    if steps:
        lines.extend(["", "**What to do**", ""])
        lines.extend("- " + step for step in steps)
    return lines


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


#: How long qa-doctor's ONE device probe may take. Small, because this report is
#: what a tester runs when nothing works.
_MOBILE_PROBE_S = 8.0
