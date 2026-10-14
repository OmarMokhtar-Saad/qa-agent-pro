"""Host mode: read the untrusted image-description field into a HostImageResult (split from host_image.py)."""

from __future__ import annotations

import logging

from agents.host_image_parse import (
    _collect_image_entry,
    _finish_image_result,
    _ImageSlot,
)
from agents.host_image_result import _IMG_MAX_ITEMS, HostImageResult, _img_note
from config.settings_guard import never_raise

logger = logging.getLogger("agents.host_mode")


def _bounded_image_entries(raw, res: HostImageResult) -> list | None:
    """The entries to read (capped at _IMG_MAX_ITEMS), or None for absent/non-list."""
    if raw is None:
        return None
    if not isinstance(raw, list):
        _img_note(
            res,
            "`image_descriptions` was not a list -- the whole field was "
            "ignored. No descriptions were recorded and none were invented.",
        )
        return None
    entries = list(raw)
    if len(entries) > _IMG_MAX_ITEMS:
        _img_note(
            res,
            f"`image_descriptions` carried {len(entries)} entries -- only the "
            f"first {_IMG_MAX_ITEMS} were read.",
        )
        entries = entries[:_IMG_MAX_ITEMS]
    return entries


# Validate the SHAPE of the UNTRUSTED top-level `image_descriptions` field.
#
# NEVER raises and NEVER trusts the field. Rules, enforced here in Python over
# already-``json.loads``'d data (no eval, no dynamic attribute access), and
# deliberately mirroring extract_host_acs:
#
#   * absent / None                 -> ran=False, no notes (the common case)
#   * not a list                    -> ran=False + note
#   * a string entry                -> tolerated as its description
#   * a dict entry                  -> `description` (or `text`), optional
#                                      `image_id` (or `filename`)
#   * any other entry type          -> dropped + counted
#   * a description under 5 chars   -> dropped + counted
#   * beyond _IMG_MAX_ITEMS         -> truncated + noted
#   * ZERO surviving descriptions   -> ran=False + note
#
# Ids are never trusted as identifiers: an unusable or missing one is replaced
# with the entry's 1-based position, so a forged id cannot re-point a row.
def extract_host_image_descriptions(
    raw, *, requested: bool = True, relevance: bool = False
) -> HostImageResult:
    """Validate the SHAPE of the UNTRUSTED top-level `image_descriptions` field."""
    res = HostImageResult(
        requested=bool(requested), relevance_requested=bool(relevance)
    )
    with never_raise(logger, "extract_host_image_descriptions failed", exc_info=True):
        entries = _bounded_image_entries(raw, res)
        if entries is None:
            return res
        for pos, entry in enumerate(entries, start=1):
            _collect_image_entry(res, _ImageSlot(entry, pos, relevance))
        return _finish_image_result(res, relevance)
    return HostImageResult(requested=bool(requested))
