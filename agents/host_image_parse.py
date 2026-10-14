"""Host mode: parse one untrusted image entry and finish the result (split from host_image.py)."""

from __future__ import annotations

import dataclasses

from agents.host_image_result import (
    _IMG_MAX_ID_CHARS,
    _IMG_MAX_REASON_CHARS,
    _IMG_MIN_DESC_CHARS,
    _IMG_RELEVANCE_VALUES,
    HostImageResult,
    _img_clean,
    _img_note,
    _img_relevance_gap_note,
)


@dataclasses.dataclass(frozen=True)
class _ImageSlot:
    """One raw entry with its 1-based position and whether a verdict was asked for."""

    entry: object
    pos: int
    relevance: bool


# STRING gate FIRST, then an identity ENUM lookup. Both are
# load-bearing: _img_clean does str(text or ""), so without
# the isinstance guard a JSON boolean `true` would arrive as
# the token "true" -- and with a non-identity map that read
# as `yes` and SUPPRESSED the off-topic warning. Anything
# that is not one of the three bare words now records NO
# verdict instead of an answer.
def _image_verdict(entry: dict) -> tuple[str, str]:
    """``(verdict, reason)`` of one dict entry; the verdict is "" when none resolved."""
    verdict = ""
    raw_rel = entry.get("relevant")
    if isinstance(raw_rel, str):
        verdict = _IMG_RELEVANCE_VALUES.get(_img_clean(raw_rel, 32).strip().lower(), "")
    reason = _img_clean(
        entry.get("relevance_reason") or entry.get("reason") or "",
        _IMG_MAX_REASON_CHARS,
    )
    return verdict, reason


def _parse_image_entry(entry, relevance: bool) -> tuple[str, str, str, str] | None:
    """``(img_id, desc, verdict, reason)`` for one entry; None for a bad type."""
    if isinstance(entry, str):
        return "", _img_clean(entry), "", ""
    if not isinstance(entry, dict):
        return None
    img_id = _img_clean(
        entry.get("image_id") or entry.get("filename") or "", _IMG_MAX_ID_CHARS
    )
    desc = _img_clean(entry.get("description") or entry.get("text") or "")
    verdict, reason = _image_verdict(entry) if relevance else ("", "")
    return img_id, desc, verdict, reason


def _collect_image_entry(res: HostImageResult, slot: _ImageSlot) -> None:
    """Add one usable entry to ``res.images`` (and ``off_topic``), else count a drop."""
    parsed = _parse_image_entry(slot.entry, slot.relevance)
    if parsed is None or len(parsed[1]) < _IMG_MIN_DESC_CHARS:
        res.dropped += 1
        return
    img_id, desc, verdict, reason = parsed
    item = {"image_id": img_id or str(slot.pos), "description": desc}
    # Attached ONLY when a verdict actually resolved, so with relevance
    # off (or a prep that never asked) every item is the exact two-key
    # dict this function has always produced -- flag-OFF byte identity.
    if verdict:
        item["relevant"] = verdict
        item["relevance_reason"] = reason
    res.images.append(item)
    if verdict in ("no", "unsure"):
        res.off_topic.append(dict(item))


# PER-IMAGE, not all-or-nothing (review finding M6): a host that
# judged image 1 and skipped image 2 used to leave image 2 untagged
# with nothing said about it -- the same silent gap this feature
# exists to close. Said out loud instead: with no verdict this server
# cannot claim the screen matches the ticket, and it made no vision
# call of its own to check.
def _note_image_gaps(res: HostImageResult, relevance: bool) -> None:
    """Note drops, and per-image verdict gaps when a verdict was asked for."""
    if res.dropped:
        _img_note(
            res,
            f"{res.dropped} entr{'y was' if res.dropped == 1 else 'ies were'} "
            "dropped as unreadable or too short.",
        )
    if relevance and res.images:
        gap_note = _img_relevance_gap_note(res.images)
        if gap_note:
            _img_note(res, gap_note)


def _finish_image_result(res: HostImageResult, relevance: bool) -> HostImageResult:
    """Note drops and verdict gaps, then mark ``res`` ran when any image survived."""
    _note_image_gaps(res, relevance)
    if not res.images:
        if not res.notes:
            _img_note(
                res,
                "`image_descriptions` carried no usable description -- none "
                "were recorded and none were invented.",
            )
        return res
    res.ran = True
    return res
