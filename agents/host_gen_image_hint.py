"""Host mode: the pending-image-gate heads-up for duplicate clarifies (C3c).

Moved from handlers/generate.py. The gate condition has ONE definition
(``image_gate_would_fire``) shared by the gate and the hint, because a
hand-written mirror drifted three times in one day. Every call that reaches a
handler module goes through the hooks passed in, so patches still bite.
"""

from __future__ import annotations

import logging
from typing import Any

from agents.host_gen_args import GateProbe, HintArgs
from agents.host_gen_hooks import CaptureHooks, GenerateHooks, ImageHooks
from config.settings_guard import never_raise

logger = logging.getLogger("agents.host_mode")

_HINT_TEXT = (
    "\n\n> \U0001f4f8 **Heads-up: I have not asked about this ticket's "
    "SCREENS yet.** That question comes next, so a bare retry spends "
    "another round trip on it. If the user has already said where the "
    "screens come from, send `source_plan` on the SAME retry -- `jira` "
    "(ticket text only), `jira_attach`, `jira_device`, `jira_both` or "
    "`device`. Do NOT guess it if they have not said."
)


def image_gate_would_fire(p: GateProbe, image: ImageHooks) -> bool:
    """THE condition for the Jira image gate; callers pass normalized values.

    Whitespace is not a ticket: "   " is truthy and used to suppress the
    pre-fetch ask on what is still a first call. Never raises.
    """
    # pragma: no cover - defensive; a gate never breaks a prepare
    with never_raise(logger, "_image_gate_would_fire failed", exc_info=True):
        return bool(
            not p.plan
            and not str(p.jira_content_json or "").strip()
            and not p.attached_images
            and not p.attested
            and image.gate_jira_source(p.text)
        )
    return False


# Normalize EXACTLY as the gate does before asking, or the predicate is shared
# in name only: drop empty entries (C5) -- [None] / [b""] / [""] is not a
# screen; resolve captures the way the gate WILL see them (tray OR the
# carry-forward shelf, without moving anything), because the hint promising a
# screens question that never comes is worse than a false negative.
def supplied_images_for_hint(
    attached_images: Any, capture_ids: Any, capture: CaptureHooks
) -> list:
    """The screens the image gate WILL see, for pending_image_gate_hint."""
    supplied = [i for i in (attached_images or []) if i]
    if not supplied and capture_ids:
        supplied = list(capture.resolvable(list(capture_ids)))
    return supplied


def _attested_from(attached_image_count: Any) -> int:
    try:
        return max(0, int(attached_image_count or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def pending_image_gate_hint(a: HintArgs, hooks: GenerateHooks) -> str:
    """Heads-up that the Jira image gate is STILL AHEAD, appended to a
    duplicate-prep / duplicate-suite clarify. "" when the gate would not fire
    anyway. Never raises."""
    # pragma: no cover - a hint never breaks a prepare
    with never_raise(logger, "_pending_image_gate_hint failed", exc_info=True):
        attested = _attested_from(a.attached_image_count)
        supplied = supplied_images_for_hint(
            a.attached_images, a.capture_ids, hooks.capture
        )
        plan = hooks.image.normalize_source_plan(a.source_plan)
        probe = GateProbe(a.text, plan, a.jira_content_json, supplied, attested)
        if not image_gate_would_fire(probe, hooks.image):
            return ""
        return _HINT_TEXT
    return ""
