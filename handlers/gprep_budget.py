"""Per-result image budget verdicts for the host-mode PREPARE reply."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise
from handlers import gprep_limits
from tools import settings_port

logger = logging.getLogger("tools.mcp_handlers")


def _classify(img, used: int, slots: int) -> tuple[str, str, int]:
    """One image -> (verdict, name, size); verdict is keep, drop or skip."""
    if not isinstance(img, dict):
        return "skip", "", 0
    data = img.get("data")
    name = img.get("filename") or "attachment"
    if not isinstance(data, (bytes, bytearray)) or not data:
        return "skip", name, 0
    if slots <= 0 or used + len(data) > gprep_limits._MAX_IMAGE_RESULT_BYTES:
        return "drop", name, 0
    return "keep", name, len(data)


# POSITIONAL verdicts for the per-result image budget: ``(flags, dropped)``.
#
# Split out of _select_prepare_images (2026-08-09, review M1) so the PREPARE
# stamp can ask "how many of these will actually ship?" using the very code
# that later decides it, instead of a second copy that drifts. ``flags[i]`` is
# True when ``images[i]`` rides on the reply; ``dropped`` names the images
# refused for size or for the jira_max_images count cap, in order. Pure.
def _budget_keep_flags(images: list) -> tuple[list[bool], list[str]]:
    """POSITIONAL verdicts for the per-result image budget: (flags, dropped)."""
    flags: list[bool] = []
    dropped: list[str] = []
    used = 0
    slots = max(0, int(getattr(settings_port.get(), "jira_max_images", 0) or 0))
    for img in images or []:
        verdict, name, size = _classify(img, used, slots)
        flags.append(verdict == "keep")
        if verdict == "drop":
            dropped.append(name)
        if verdict == "keep":
            used += size
            slots -= 1
    return flags, dropped


def _sum_kept(imgs: list, flags: list[bool]) -> tuple[int, int]:
    """(count, raw_bytes) of the images whose flag is True."""
    total = 0
    kept = 0
    for img, ok in zip(imgs, flags):
        if not ok:
            continue
        kept += 1
        data = img.get("data")
        if isinstance(data, (bytes, bytearray)):
            total += len(data)
    return (kept, total)


# ``(count, raw_bytes)`` for the images that will ACTUALLY ride on this
# reply.
#
# Decided by ``_budget_keep_flags`` -- the very function that later drops
# them -- so the handover disclosure cannot bill the tester for an
# attachment the per-result byte budget or the ``jira_max_images`` count cap
# refuses. A second copy of those rules would drift; this has no copy at all.
# Never raises: a count may not break a prepare, and 0 under-reports rather
# than inventing bytes.
def _kept_image_bytes(images: list) -> tuple[int, int]:
    """(count, raw_bytes) for the images that will ACTUALLY ride on this reply."""
    with never_raise(logger, "counting the images that ship failed", exc_info=True):
        imgs = list(images or [])
        flags, _dropped = _budget_keep_flags(imgs)
        return _sum_kept(imgs, flags)
    return (0, 0)


# How many DEVICE-captured screens actually ride on this reply.
#
# 2026-08-09 (review M1). The prepare envelope used to stamp the count read off
# the capture tray, but _select_prepare_images caps the reply at
# jira_max_images (default 3) -- so a 5-screen capture stamped 5, shipped 3,
# and the submit-side attested_image_gap_note reported a shortfall that no
# action could ever clear. Matched by OBJECT IDENTITY, because the captured
# dicts are the same objects that were merged into the forwarded list, so no
# filename collision can miscount. Never raises: on any internal error it falls
# back to the pre-fix count, i.e. today's behaviour.
def _shipped_capture_count(images: list, captured: list) -> int:
    """How many DEVICE-captured screens actually ride on this reply."""
    with never_raise(logger, "counting shipped captured screens failed", exc_info=True):
        wanted = {id(i) for i in (captured or []) if isinstance(i, dict)}
        if not wanted:
            return 0
        imgs = list(images or [])
        flags, _dropped = _budget_keep_flags(imgs)
        return sum(1 for img, ok in zip(imgs, flags) if ok and id(img) in wanted)
    return len([i for i in (captured or []) if i])
