"""The ticket's own image labels, and how a capture's screens get their names."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise
from handlers import capture_limits as _limits
from handlers import image_gate
from tools import clock

logger = logging.getLogger("tools.mcp_handlers")


def _shelve_ticket_image_labels(names: list, source: str) -> None:
    """Record the ticket's own screen labels for the NEXT capture. Never raises."""
    with never_raise(logger, "shelving ticket image labels failed", exc_info=True):
        clean = [image_gate._safe_image_name(n) for n in list(names or [])[:8]]
        clean = [n for n in clean if n]
        if not clean:
            return
        _limits._TICKET_IMAGE_LABELS["labels"] = clean
        _limits._TICKET_IMAGE_LABELS["at"] = clock.now()
        _limits._TICKET_IMAGE_LABELS["source"] = str(source or "")[:120]


def _take_ticket_image_labels() -> tuple:
    """Pop the shelved labels if still fresh; returns ``(labels, source)``.

    Shares the capture tray's TTL: labels older than the capture_ids they would
    name are useless. Never raises."""
    with never_raise(logger, "reading ticket image labels failed", exc_info=True):
        at = float(_limits._TICKET_IMAGE_LABELS.get("at") or 0.0)
        labels = list(_limits._TICKET_IMAGE_LABELS.get("labels") or [])
        source = str(_limits._TICKET_IMAGE_LABELS.get("source") or "")
        _limits._TICKET_IMAGE_LABELS["labels"] = []
        _limits._TICKET_IMAGE_LABELS["at"] = 0.0
        _limits._TICKET_IMAGE_LABELS["source"] = ""
        if not labels or (clock.now() - at) > _limits._CAPTURE_TRAY_TTL_S:
            return [], ""
        return labels, source
    return [], ""


def _typed_names(names: object) -> list:
    """The tester's own names (comma separated), cleaned."""
    typed = [image_gate._safe_image_name(p) for p in str(names or "").split(",")]
    return [p for p in typed if p]


def _consume_labels() -> tuple:
    """``(labels, label_note)`` from the shelved ticket labels (POPS them)."""
    shelved, source = _take_ticket_image_labels()
    if not shelved:
        return [], ""
    src_name = image_gate._safe_image_name(source) or "the ticket"
    return shelved, f" (from the labels on `{src_name}`)"


# ``(labels, label_note)`` for the captured screens. NOT pure: with no
# typed names it CONSUMES the shelved ticket labels, so call it only once
# a capture has succeeded.
#
# K2 (2026-08-10): no elicitation. Names resolve server-side:
#   1. the `names` parameter (the tester's own words, via chat)
#   2. the ticket's own labels, shelved by image-gate beat 2
#   3. screen_N -- already _stash_captures' default
def _capture_labels(names: object, n_screens: int) -> tuple:
    """``(labels, label_note)`` for the captured screens. NOT pure: see above."""
    labels: list = []
    label_note = ""
    typed = _typed_names(names)
    if typed:
        labels = typed
    else:
        labels, label_note = _consume_labels()
    if labels and len(labels) < n_screens:
        # _stash_captures indexes positionally, so the surplus screens keep
        # screen_N. Say so rather than letting the tester assume naming failed.
        label_note += (
            f" — {len(labels)} name(s) for {n_screens} screen(s), so the "
            "rest keep their default names"
        )
    return labels, label_note
