"""Suite-submission replies and size notes (moved from handlers._reply)."""

from __future__ import annotations

import json
import logging
from typing import Any

from agents import host_category_jobs
from agents.host_echo import _UNREC_SHOWN
from agents.host_echo import clip_echo as _clip_echo
from config.settings_guard import never_raise
from tools import settings_port

logger = logging.getLogger("tools.mcp_handlers")


# Distinguishes "this exit declared no recovery target" from "this exit forgot
# to declare one". See the invariant at the `kind == "error"` exit of
# `handle_prepare_api_tests`.
_NO_RECOVERY_DECLARED = object()


def _canonical_name(raw: object, normalize) -> str | None:
    """One staged name, canonical where it normalises; None when blank."""
    text = str(raw or "").strip()
    if not text:
        return None
    with never_raise() as g:
        canon = normalize(text)
    if g.failed:
        canon = ""
    return canon or _clip_echo(text, 60)


# Canonical where a name normalises, bounded and fence-safe where it does not,
# and capped either way. Shares `_UNREC_SHOWN` and `_clip_echo` with
# `_unrecognized_names_line` so the two cannot drift -- drift between two
# renderers of the same values is precisely what round 3 found. Never raises;
# the caller renders the COUNT separately, so an empty return still leaves a
# correct reply.
def _staged_names_suffix(names: Any) -> str:
    """`" (A, B, C)"` for the staged-category names, or `""`."""
    with never_raise(logger, "could not render staged category names", exc_info=True):
        out = [
            name
            for name in (
                _canonical_name(r, host_category_jobs.normalize_category)
                for r in names or []
            )
            if name is not None
        ]
        if not out:
            return ""
        shown = out[:_UNREC_SHOWN]
        rest = len(out) - len(shown)
        return " (" + ", ".join(shown) + (f", +{rest} more" if rest > 0 else "") + ")"
    return ""


# Mirrors that function's two branches EXACTLY, which is the whole point:
#     * a str  -> ``len(obj.encode("utf-8", "ignore"))``
#     * a dict -> ``len(json.dumps(obj, ensure_ascii=False).encode(...))``
#
# A single ``json.dumps`` for both shapes is what the first attempt at the
# Path-B bound did, and it RE-ESCAPED an already-serialised submission: a
# 57,129-byte payload measured 64,297, so the refusal's number was wrong and
# the effective cap ~11% tighter than the configured one. The invariant that
# came out of it -- measure a size bound in the representation the cap is
# denominated in -- is why this helper exists rather than another caller of
# the sidecar one. Returns 0 for anything unmeasurable; never raises.
def _submission_bytes(obj: object) -> int:
    """The size of a host submission AS ``parse_host_suite`` measures it."""
    try:
        if isinstance(obj, str):
            return len(obj.encode("utf-8", "ignore"))
        if isinstance(obj, dict):
            return len(json.dumps(obj, ensure_ascii=False).encode("utf-8", "ignore"))
    except (TypeError, ValueError, RecursionError):
        return 0
    return 0


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
def oversized_full_suite_note(obj: object) -> str:
    """``""`` if a FULL (Path-B) submission is within the cap, else a refusal.

    Deliberately separate from ``oversized_submission_note``: that one speaks
    about "the review fields in this submission", which is false here -- this
    object is the whole suite, test cases included -- and the second invariant
    from the reverted attempt is that a refusal may only assert facts its own
    branch knows. Never raises.
    """
    cap = int(getattr(settings_port.get(), "qa_prep_max_bytes", 0) or 0)
    if not cap or obj is None:
        return ""
    size = _submission_bytes(obj)
    if size <= cap:
        return ""
    return _full_suite_refusal(size, cap)


def _full_suite_refusal(size: int, cap: int) -> str:
    """The refusal text for a FULL submission over the byte cap."""
    return (
        f"\u26a0\ufe0f That suite is {size} bytes, over the {cap}-byte limit for a "
        "single submission. Nothing was discarded. Send it as per-category "
        "submissions with `qa_submit_category` (each category is measured on its "
        "own), then finalize with `qa_submit_suite` and the same prep_id -- the "
        "server's own merge of accepted categories is NOT subject to this limit."
    )


def _unreadable_text(ident: str, exists: bool, total: int) -> str:
    """The reply text for a suite id that yielded no readable rows."""
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


def _suite_unreadable_reply(suite_id: str, loaded: Any) -> str:
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
    except (AttributeError, TypeError, ValueError):  # pragma: no cover - defensive
        exists, total = False, 0
    return _unreadable_text(ident, exists, total)
