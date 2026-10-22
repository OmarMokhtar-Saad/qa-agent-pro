"""Refuse-once gates of qa_submit_suite: contradicted duplicate review, risky quality.

K-09 / K-13. The duplicate gate REFUSES a contradicted review once and
says why; the quality gate refuses every call that lacks the host's confirmation
and refuses a pre-emptive confirmation once (the same two beats).
The refusal is stamped on the prep (``meta``); a later call is accepted when the
host answers it in the review sidecar: ``duplicate_justifications`` (a reason per
listed pair) or ``confirm_quality_warnings: true``. Nothing is discarded by a
refusal, so a gate can never block forever. A ``str`` return is the refusal.
"""

from __future__ import annotations

import logging
import re
from typing import Any, NamedTuple

from agents import host_submit_dup_notes
from agents.host_dedup_render import shortlist_safe
from agents.host_submit_ports_prep import SubmitPorts
from agents.host_submit_types import SubPrep
from config.settings_guard import never_raise
from tools import prep_store

logger = logging.getLogger("agents.host_submit_suite_gate")

# Invented exact UI strings at or above this count, or untraced cases at or above
# this share of the suite, ask the host before the workbook is written.
INVENTED_STRINGS_MAX = 8
UNTRACED_SHARE_MAX = 0.25
UNTRACED_TRIGGER_CASES = 5
_REASON_CHARS = 10
_TITLE_CAP = 80
_STRINGS_RE = re.compile(r"(\d+) exact UI string\(s\) are asserted")
_ORPHANS_RE = re.compile(r"(\d+) of (\d+) case\(s\) trace to NO acceptance")


class GateCtx(NamedTuple):
    prep: SubPrep
    suite: Any
    parsed: Any
    dup: Any


async def _stamp(prep: SubPrep, key: str) -> bool:
    """Persist the refusal stamp; False when it could not be stored.

    A refusal that could not be stamped could never be confirmed, so the callers
    let the submission through instead of refusing it.
    """
    with never_raise(logger, "suite gate stamp failed", exc_info=True):
        meta = {**prep.meta, key: True}
        out = await prep_store.update_prep(
            prep.prep_id, {**prep.envelope, "meta": meta}
        )
        if (out or {}).get("error"):
            logger.warning("suite gate stamp %s not stored: %s", key, out["error"])
            return False
        return True
    return False


def _pair_key(a: object, b: object) -> frozenset:
    return frozenset((str(a).strip().upper(), str(b).strip().upper()))


def _justified(raw: object) -> set:
    """Pair keys the host justified with a real reason."""
    out: set = set()
    for item in raw if isinstance(raw, list) else []:
        ids = item.get("ids") or item.get("pair") if isinstance(item, dict) else None
        reason = str(item.get("reason") or "") if isinstance(item, dict) else ""
        if (
            isinstance(ids, list)
            and len(ids) == 2
            and len(reason.strip()) >= _REASON_CHARS
        ):
            out.add(_pair_key(*ids))
    return out


def _safe(text: object, cap: int) -> str:
    return shortlist_safe(text, cap).replace('"', "'")


def _pair_lines(pairs: list) -> str:
    return "\n".join(
        f'- `{_safe(p.get("id_a"), 24)}` "{_safe(p.get("title_a"), _TITLE_CAP)}" '
        f'~ `{_safe(p.get("id_b"), 24)}` "{_safe(p.get("title_b"), _TITLE_CAP)}"'
        for p in pairs
    )


def _dup_refusal(ctx: GateCtx, pairs: list) -> str:
    return (
        f"{ctx.prep.version_note}⛔ **Duplicate review contradicted -- "
        "resubmit once.** You reported no duplicates, but the server's own "
        f"title comparison found {len(pairs)} candidate pair(s):\n\n"
        f"{_pair_lines(pairs)}\n\n"
        "Merge the ones that verify the same behaviour (resend the "
        "`duplicate_groups` sidecar naming them), or keep a pair and say why: "
        'send `"duplicate_justifications": [{"ids": ["TC-001", "TC-002"], '
        '"reason": "<why both are needed>"}]`, one entry per pair. **Nothing '
        f"was discarded**: the prep `{ctx.prep.prep_id}` and its rows survive "
        "and nothing was exported.\n\n"
    )


async def dup_gate(ctx: GateCtx, ports: SubmitPorts) -> str | None:
    """Refuse once when the host's 'no duplicates' is contradicted by pairs."""
    parsed = ctx.parsed
    claimed_none = getattr(parsed, "duplicate_review_offered", False)
    if not (claimed_none and ctx.dup.review_on and not ctx.dup.groups):
        return None
    with never_raise(logger, "duplicate gate failed", exc_info=True):
        head, pairs = host_submit_dup_notes.prescreen_head(ctx.suite, ports)
        pairs = [p for p in pairs or [] if isinstance(p, dict)]
        if not head or not pairs:
            return None
        said = _justified(getattr(parsed, "raw_duplicate_justifications", None))
        left = [p for p in pairs if _pair_key(p.get("id_a"), p.get("id_b")) not in said]
        if not left or ctx.prep.meta.get("dup_contradiction_refused"):
            return None
        if not await _stamp(ctx.prep, "dup_contradiction_refused"):
            return None
        return _dup_refusal(ctx, left)
    return None


def _quality_counts(summary: object, orphan_note: str, suite: Any):
    """(invented strings, untraced cases, total cases) from the existing checks."""
    strings = _STRINGS_RE.search(str(summary or ""))
    orphans = _ORPHANS_RE.search(orphan_note or "")
    total = len(getattr(suite, "test_cases", None) or [])
    return (
        int(strings.group(1)) if strings else 0,
        int(orphans.group(1)) if orphans else 0,
        total,
    )


def _risky(strings: int, orphans: int, total: int) -> bool:
    share = orphans / total if total else 0.0
    return strings >= INVENTED_STRINGS_MAX or (
        orphans >= UNTRACED_TRIGGER_CASES and share >= UNTRACED_SHARE_MAX
    )


def _quality_refusal(ctx: GateCtx, counts: tuple[int, int, int]) -> str:
    strings, orphans, total = counts
    return (
        f"{ctx.prep.version_note}⛔ **Quality warnings need your decision "
        "before the workbook is written.**\n\n"
        f"- {strings} exact UI string(s) are asserted that the source never "
        "promises (listed under *Suite Consistency* in the reply you get once "
        "finalized).\n"
        f"- {orphans} of {total} case(s) trace to no acceptance criterion.\n\n"
        "Fix them (assert what the message must say instead of its exact "
        "text; set each case's `requirement_id`) and resubmit under the SAME "
        f"prep_id `{ctx.prep.prep_id}`, or keep the suite as is by sending "
        '`"confirm_quality_warnings": true` in the sidecar after asking the '
        "tester. **Nothing was discarded** and nothing was exported.\n\n"
    )


async def quality_gate(ctx: GateCtx, ports: SubmitPorts, summary: object) -> str | None:
    """Refuse once when invented strings or untraced cases pass the threshold."""
    with never_raise(logger, "quality warning gate failed", exc_info=True):
        note = ports.prep_gate.rtm_orphan_note(ctx.suite)
        counts = _quality_counts(summary, note, ctx.suite)
        if not _risky(*counts):
            return None
        confirmed = getattr(ctx.parsed, "raw_confirm_warnings", None) is True
        if confirmed and ctx.prep.meta.get("quality_warning_refused"):
            return None
        if not await _stamp(ctx.prep, "quality_warning_refused"):
            return None
        return _quality_refusal(ctx, counts)
    return None
