"""Host mode: tally and pick image relevance verdicts (split from host_image.py)."""

from __future__ import annotations

import logging

from agents.host_image_result import _IMG_RELEVANCE_VALUES
from config.settings_guard import never_raise

logger = logging.getLogger("agents.host_mode")


_IMG_COUNT_ZERO: dict = {
    "images": 0,
    "verdicts": 0,
    "no": 0,
    "unsure": 0,
    "ran": False,
}


def _tally_verdict(out: dict, item: object) -> None:
    """Count one already-validated image item's verdict into ``out``."""
    if not isinstance(item, dict):
        return
    verdict = item.get("relevant")
    if not isinstance(verdict, str) or verdict not in _IMG_RELEVANCE_VALUES:
        return
    out["verdicts"] += 1
    if verdict in ("no", "unsure"):
        out[verdict] += 1


# Pure tally of the ALREADY-VALIDATED relevance verdicts. Never raises.
#
# Reads only ``HostImageResult.images``, whose entries carry a ``relevant``
# key ONLY when extract_host_image_descriptions resolved it through the
# isinstance-str gate and the strict three-word identity map. Nothing here
# re-interprets an untrusted token, and a membership test against
# ``_IMG_RELEVANCE_VALUES`` is repeated as belt-and-braces so a future caller
# cannot smuggle a value in by constructing the dataclass by hand.
#
# Returns ``{"images", "verdicts", "no", "unsure", "ran"}``. ``ran`` means "at
# least one USABLE verdict came back", which is deliberately NOT
# ``HostImageResult.ran`` ("at least one usable DESCRIPTION came back"): the
# submit audit row and the Batch-4 enforcement gate both need the former, and
# conflating them is what made a forfeited check read like a passed one for
# the ambiguity job (FIX 2, 2026-08-09).
def image_relevance_counts(result) -> dict:
    """Pure tally of the ALREADY-VALIDATED relevance verdicts. Never raises."""
    out = dict(_IMG_COUNT_ZERO)
    with never_raise(logger, "image_relevance_counts failed", exc_info=True):
        imgs = list(getattr(result, "images", None) or [])
        out["images"] = len(imgs)
        for item in imgs:
            _tally_verdict(out, item)
        out["ran"] = out["verdicts"] > 0
        return out
    return dict(_IMG_COUNT_ZERO)


def off_topic_images(result) -> list:
    """The entries whose verdict is a hard ``no``. Never raises.

    NARROWER than ``HostImageResult.off_topic``, which also collects ``unsure``
    so the tester-facing warning can mention it. The Batch-4 refusal must key on
    ``no`` alone -- refusing on uncertainty punishes the honest answer and
    teaches a host that ``yes`` is the cheap one. Returns COPIES, like
    ``off_topic`` itself, so a caller cannot mutate the parsed result.
    """
    with never_raise(logger, "off_topic_images failed", exc_info=True):
        return [
            dict(i)
            for i in (list(getattr(result, "images", None) or []))
            if isinstance(i, dict) and i.get("relevant") == "no"
        ]
    return []
