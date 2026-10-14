"""Host mode: open-prep lookup and carry-forward merge for the preflight (C3c).

Moved from handlers/generate.py. ``find_open_prep`` is a pure leaf over
``tools.prep_store``; ``merge_carry_forward`` reaches the capture module only
through the CaptureHooks passed in.
"""

from __future__ import annotations

import logging

from agents.host_gen_args import CarryArgs, CarryAsk
from agents.host_gen_hooks import CaptureHooks
from agents.host_gen_types import _PreflightCarry
from config.settings_guard import never_raise
from tools import prep_store, settings_port

logger = logging.getLogger("agents.host_mode")


# 2026-08-03: the duplicate guard used to query only finished suites. A real
# run made two preps 43s apart for a byte-identical source, was told nothing,
# and discarded a whole preparation.
async def find_open_prep(text: str) -> dict | None:
    """A recent unfinalized prep for this exact source, or None. Never raises."""
    _window = max(
        0, int(getattr(settings_port.get(), "qa_host_duplicate_prep_window_s", 1800))
    )
    with never_raise(logger, "recent-prep duplicate check failed", exc_info=True):
        _recent = await prep_store.find_recent_prep_by_source(text, _window)
        return (_recent or {}).get("content")
    return None


def _append_carried(carry: _PreflightCarry, more_carried: list, frm: str) -> None:
    # APPEND: ids revived from a re-sent list and ids recovered from
    # the prior prep are different findings and both are stamped.
    carry.carried_ids = list(carry.carried_ids) + [
        c for c in more_carried if c not in carry.carried_ids
    ]
    carry.carried_from = frm


# 2026-08-09: losing the previous prep's screens is the silent harm the
# duplicate guard exists to prevent. Unlike the open-prep clarify this runs
# REGARDLESS of `proceed_anyway` and takes its own `image_carry_ack=true`.
# It revives BEFORE its refusal, deliberately (see "Deferred REVIVE").
def merge_carry_forward(a: CarryArgs, capture: CaptureHooks) -> str:
    """Fold the carry-forward verdict on a.prep into a.carry; refusal text or ""."""
    carry = a.carry
    _ids, _more_carried, _from, _note, _refusal = capture.carry_forward(
        CarryAsk(
            a.prep,
            image_carry_ack=a.image_carry_ack,
            capture_ids=carry.capture_ids,
            have_attested=a.have_attested,
        )
    )
    if _ids:
        carry.capture_ids = list(_ids)
    if _more_carried:
        _append_carried(carry, _more_carried, _from)
    if _note:
        carry.carry_note = (
            (carry.carry_note + "\n\n" + _note) if carry.carry_note else _note
        )
    return _refusal or ""
