"""Image relevance verdict and findings text for the QA_HOST_IMAGE_REQUIRE_RELEVANT gate."""

from __future__ import annotations

import logging

from agents.host_image import image_relevance_counts, off_topic_images

logger = logging.getLogger("agents.host_mode")

# Batch 4, LAYER 2 (QA_HOST_IMAGE_REQUIRE_RELEVANT, default OFF).
_IMAGE_GATE_MAX_NAMED = 8


def _named_off_topic(off: list) -> str:
    """The off-topic screens, one bullet each, capped at _IMAGE_GATE_MAX_NAMED."""
    named = "\n".join(
        f"   - `{i.get('image_id', '?')}` — "
        f"{i.get('relevance_reason', '') or i.get('description', '')}"
        for i in off[:_IMAGE_GATE_MAX_NAMED]
    )
    if len(off) > _IMAGE_GATE_MAX_NAMED:
        named += f"\n   - (+{len(off) - _IMAGE_GATE_MAX_NAMED} more)"
    return named


def _off_topic_facts(counts: dict, off: list) -> str:
    """The finding when the host marked screens ``relevant: "no"``."""
    return (
        f"- **{len(off)} of {counts.get('images', 0)} described "
        'screen(s) came back `relevant: "no"`** -- your own chat '
        "model's verdict, MODEL-DERIVED and not verified by this "
        "server, which made no vision call:\n" + _named_off_topic(off)
    )


def _silence_facts(forwarded: int) -> str:
    """The finding when no usable verdict came back for any image."""
    return (
        f"- **No usable `relevant` verdict came back for any image**, "
        f"although this prep forwarded {forwarded} screen(s) to your "
        "chat and asked for one per image. This server made no vision "
        "call of its own, so there is NO record that the screens were "
        "looked at -- and silence must not read as `yes`."
    )


def relevance_facts(forwarded: int, counts: dict, off: list) -> str:
    """The findings block of image_relevance_gate: the off-topic screens, or the
    absence of any usable verdict."""
    if off:
        return _off_topic_facts(counts, off)
    return _silence_facts(forwarded)


def _forwarded_count(meta: dict) -> int:
    """Screens this prep forwarded to the host: captured plus attached."""
    try:
        return int(meta.get("captured_image_count") or 0) + int(
            meta.get("attached_image_count") or 0
        )
    except (TypeError, ValueError, OverflowError):
        return 0


def _stamped_meta(meta: object) -> "dict | None":
    """The meta when it stamped an enforcement contract and forwarded screens."""
    if not isinstance(meta, dict):
        return None
    if not meta.get("host_image_require_relevant"):
        return None
    if not meta.get("host_image_relevance"):
        return None
    return meta if _forwarded_count(meta) > 0 else None


def _forgiven_absence(counts: dict, meta: dict) -> bool:
    """True when an ABSENT verdict is forgiven (a passed submit stamped it)."""
    # Review H1: a resubmit is not asked to resend `image_descriptions`,
    # so an ABSENT field on a later submit means "the server did not ask
    # for it", NOT "the check was forfeited". Refusing there would reject
    # a suite THIS gate had already passed. The submit that DID answer
    # stamps `image_relevance_seen` -- the same carry-forward
    # discipline as serialize_adopted_state and the carried checklist
    # beside it. Only ABSENCE is forgiven: a verdict that IS resent is
    # judged normally, so a fresh `no` on a later round still refuses.
    return not counts.get("ran") and bool(meta.get("image_relevance_seen"))


def relevance_verdicts(meta: object, result: object) -> "tuple | None":
    """What image_relevance_gate needs to judge a submission, or None when the
    gate passes outright. Returns (forwarded, counts, off, refused_before).
    Called inside the gate's ``try``, so an error still fails OPEN there."""
    stamped = _stamped_meta(meta)
    if stamped is None:
        return None
    counts = image_relevance_counts(result)
    if counts.get("ran") and not counts.get("no"):
        return None
    if _forgiven_absence(counts, stamped):
        return None
    refused = bool(stamped.get("image_relevance_refused"))
    return _forwarded_count(stamped), counts, off_topic_images(result), refused
