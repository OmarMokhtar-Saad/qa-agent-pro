"""Naming the capture ids a prepare could not resolve, and why."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise
from handlers import capture_limits as _limits
from handlers.capture_evict import _eviction_reason, _over_cap_ids

logger = logging.getLogger("tools.mcp_handlers")


def _missing_line(entry: object, over_cap: set) -> str:
    """One missing id with the reasons it contributed no screen, when known."""
    cid = str(entry or "")
    reasons = []
    if cid in over_cap:
        reasons.append(f"beyond the {_limits.tray_max()}-screen per-call cap")
    # BOTH when both apply (round-5 review): an id pushed out of the
    # tray AND beyond this call's cap needs both facts, because the
    # remedy for the first (re-capture) is not the remedy for the
    # second (send fewer ids).
    ledger = _eviction_reason(cid)
    if ledger:
        reasons.append(ledger)
    return f"{cid} ({'; also '.join(reasons)})" if reasons else cid


def _describe_missing(missing: list | None, sent: list | None = None) -> list:
    """The tester-facing rendering of ``peek_captures``' *missing* list: each
    bare id with the reason it contributed no screen beside it, when one is
    known. Two kinds of reason, from two places: PER-CALL (beyond this call's
    cap -- derived from *sent*, the call's own ``capture_ids``) and PER-ID (the
    eviction ledger: pushed out to make room, or left on the shelf because it
    did not fit). Neither is "unknown or expired", and re-sending the id
    unchanged will not help; a bare id is the honest remainder. Never raises."""
    with never_raise():
        over_cap = set(_over_cap_ids(sent))
        return [_missing_line(entry, over_cap) for entry in list(missing or [])]
    return [str(e) for e in list(missing or [])]


# Only the over-cap ids the slice did NOT already name are counted in
# the tail: an id shown with its reason inline and counted again was
# double reporting (round-6 review).
#
# No "is it in `missing`?" filter here, and that is deliberate:
# `peek_captures` appends EVERY id past `_CAPTURE_TRAY_MAX` to
# `missing`, so the over-cap ids of a call are always a subset of that
# call's missing list and the filter could never reject anything. It
# graded DEAD -- removing it left the whole non-mobile suite green
# (independent verification, 2026-09-07) -- and a guard that cannot
# fire is the defect rounds 13/14 removed from `wireframe` and
# `scale_bounds`, not a defence.
def _over_hidden(missing: list | None, sent: list | None) -> list:
    """The over-cap ids the shown slice did not already name."""
    named = set(str(m or "") for m in list(missing or [])[: _limits._MAX_MISSING_SHOWN])
    return [c for c in _over_cap_ids(sent) if c not in named]


def _tail_text(rest: int, over_hidden: list) -> str:
    """The ", and N more" tail plus the over-cap disclosure."""
    tail = ""
    if rest > 0:
        tail = f", and {rest} more"
    if over_hidden:
        tail += (
            f" ({len(over_hidden)} of the ids sent were beyond the "
            f"{_limits.tray_max()}-screen per-call cap -- send fewer capture_ids "
            "per prepare)"
        )
    return tail


def _render_missing(missing: list | None, sent: list | None) -> str:
    """The ONE tester-facing rendering of a missing-capture list, used by both
    replies that name them (the image-gate clarify and the prepare notice).
    Bounded to ``_MAX_MISSING_SHOWN`` names; the remainder is COUNTED, and the
    over-cap ids -- which ``peek_captures`` appends LAST, so an 8-name slice
    of a 27-id call could never show one (round-5 review) -- are counted in
    the tail only when the slice did not already name them. Never raises."""
    with never_raise():
        described = _describe_missing(missing, sent)
        shown = ", ".join(f"`{c}`" for c in described[: _limits._MAX_MISSING_SHOWN])
        rest = len(described) - _limits._MAX_MISSING_SHOWN
        return shown + _tail_text(rest, _over_hidden(missing, sent))
    return ", ".join(
        f"`{c}`" for c in list(missing or [])[: _limits._MAX_MISSING_SHOWN]
    )


def _live_ids(capture_ids: list | None) -> list:
    """The stripped ids of *capture_ids* still resident in the tray."""
    return [
        str(c or "").strip()
        for c in (capture_ids or [])
        if str(c or "").strip() in _limits._CAPTURE_TRAY
    ]


# One line telling the tester their capture ids SURVIVED a failed prepare.
#
# A prepare can fail after the screens were read (prep-store error, serde
# error, any unexpected exception). The tray is read-only until a payload
# ships, so the ids are still valid -- but nobody can tell that from
# "Preparation failed", and on the retry they would otherwise only see silent
# missing-id notes. Empty when nothing of theirs is still pending. Never
# raises.
def _capture_retry_hint(capture_ids: list | None) -> str:
    """One line telling the tester their capture ids SURVIVED a failed prepare."""
    with never_raise(logger, "_capture_retry_hint failed", exc_info=True):
        live = _live_ids(capture_ids)
        if not live:
            return ""
        return (
            f"\n\nYour {len(live)} captured screen(s) were NOT lost: "
            + ", ".join(f"`{c}`" for c in live)
            + " are still valid, so retry with the SAME `capture_ids` (they "
            f"expire {_limits._CAPTURE_TRAY_TTL_S // 60} minutes after capture)."
        )

    return ""
