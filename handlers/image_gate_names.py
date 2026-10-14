"""Jira image gate: untrusted image names and counts, and the shared image cap."""

from __future__ import annotations

from config.settings_guard import never_raise
from tools import settings_port

_IMAGE_NAME_ALLOWED = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._ -"
)


def _safe_image_name(raw) -> str:
    """Sanitize ONE ticket-supplied image name before beat 2 interpolates it.

    tools/jira_mcp._extract_image_attachments produces attachment filenames as a
    bare ``str(att.get("filename") or "attachment")`` off the HOST-submitted --
    and explicitly UNTRUSTED -- issue payload: no charset gate, no length cap.
    (Only `description_image_labels` is charset-gated there.) Beat 2 puts those
    names inside an instruction-shaped markdown block that tells the host what to
    do next, so they are allowlisted to [A-Za-z0-9._ -] and capped at 60 chars
    HERE, at the point of use -- jira_mcp is deliberately not modified by this
    plan. Never raises."""
    with never_raise():
        return "".join(
            ch for ch in str(raw or "") if ch in _IMAGE_NAME_ALLOWED
        ).strip()[:60]
    return ""


def _int_or(raw, default: int) -> int:
    """Return int(raw), or int(default) when raw cannot be coerced. Never raises on raw."""
    try:
        return int(raw)
    except (TypeError, ValueError, OverflowError):
        return int(default)


def _clamped_count(raw, *, lo: int = 0, hi: int = 99) -> int:
    """Coerce an UNTRUSTED count to a sane int inside [lo, hi]. Never raises.

    Both image gates read counts they did not produce -- `attached_image_count`
    is HOST-supplied and the prepare stamps are envelope data -- and both
    interpolate them into tester-facing prose. A string, None, a float or an
    absurd 2**40 must degrade to a number the wording can safely carry rather
    than raise inside a disclosure helper. Plain arithmetic, in the same spirit
    as the set-based sanitizers above: `re` is deliberately not imported here.
    """
    return max(lo, min(hi, _int_or(raw, 0)))


# The ONE reading of JIRA_MAX_IMAGES shared by the two halves of the
# image-COMPLETENESS contract.
#
# Scope, precisely (review N5): this used to cover TWO places that decide
# whether the screens on hand are "all of them" -- the server-side attachment
# fetch and image_gate_second_beat. Batch D1 (2026-08-15) deleted the fetch,
# and tools/jira_attachments.py with it, so only the gate reads this clamp
# today. It is KEPT and kept SHARED on purpose: a revived fetch
# (docs/RETIRED_CAPABILITIES.md) must clamp JIRA_MAX_IMAGES exactly the way
# the gate does, and clamping it differently in the two halves is the defect
# this function exists to prevent. The prepare payload's own image budget
# below is deliberately NOT rewired here; it answers a different question.
#
# Batch C, M1 (2026-08-09), the reasoning that still binds a revival: the
# fetch-completeness check and the beat-2 arithmetic in
# image_gate_second_beat are two halves of ONE contract -- "how many screens
# will this server ever carry" -- and clamping the same setting differently in
# each would let a misconfigured install fetch a number the gate then refuses
# to call complete, or the reverse. lo=1 because a cap of zero makes
# "complete" vacuous; hi=20 because that is the bound the gate has always
# applied to a tester-facing demand. Never raises (see _clamped_count).
def _jira_image_cap() -> int:
    """Return the one JIRA_MAX_IMAGES reading both completeness halves share."""
    return _clamped_count(
        _int_or(getattr(settings_port.get(), "jira_max_images", 3), 3), lo=1, hi=20
    )
