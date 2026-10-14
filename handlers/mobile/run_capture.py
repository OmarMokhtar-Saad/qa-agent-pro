"""Part of the mobile_run split (see handlers/mobile/run.py): capture."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from tools import mobile_capture as api_capture
from tools.mobile import render as mobile_render
from tools.mobile_capture import ca as capture_ca
from tools.mobile_capture import ladder
from tools.mobile_capture import ledger as capture_ledger

logger = logging.getLogger("tools.mcp_handlers")


def offer_capture(serial: str) -> bool:
    """True when this device has never been answered about.

    Read by the run reply so a tester learns the feature exists, WITHOUT the
    run stopping to ask. Silence is not consent and it is not refusal either --
    it just means nobody has been asked yet, so the offer is worth making once.
    """
    try:
        seen = capture_ledger.asked_before(serial)
        if seen.get("error"):
            return False
        return not (seen.get("content") or {}).get("asked")
    except Exception:  # pragma: no cover - the offer is never the run
        logger.exception("mobile_capture: could not decide whether to offer capture")
        return False


def _remember_capture_decline(serial: str) -> None:
    """Record a declined device so the menu is not shown again. Never raises.

    A decline that is not written down is a decline the tester gets asked about
    again on the next run, which is how a one-time question becomes a nag.
    """

    try:
        made = capture_ca.ensure_ca()
        fingerprint = str(((made.get("content") or {}).get("fingerprint")) or "")
        if fingerprint:
            entry = capture_ledger.LedgerEntry(
                serial, fingerprint, ladder.TIER_NONE, "declined by the tester"
            )
            capture_ledger.record(entry)
    except Exception:  # pragma: no cover - defensive; the record is not the run
        logger.exception("mobile_capture: could not record the capture decline")


# Notes for ``_capture_unset_stage`` (moved out of the body to keep it short):
# A device we have already answered about keeps its answer -- asking every
# run would put a blocking menu in front of every existing caller forever,
# and never asking would make the run-integrated half of this feature
# undiscoverable.
#
# The OFFER, as a record rather than a menu: the run continues and
# the status line carries one line telling the tester the feature
# exists. Returning (None, None) here is what shipped, and it made
# the whole lane invisible -- no record, no line, no capture, on
# every run.
#
# Never returns markdown here: an unset `capture` means the caller said
# nothing about capture, and a run they did not ask to change must not
# stop for a question. Only an explicit capture="on" without an ack pauses
# for consent.
def _capture_unset_stage(serial: str) -> tuple:
    """``capture`` unset: ask ONCE PER DEVICE, or carry the offer as a record."""

    seen = capture_ledger.asked_before(serial)
    if seen.get("error") or (seen.get("content") or {}).get("asked"):
        return None, None
    if offer_capture(serial):
        return None, ladder.record(
            {
                "tier": ladder.TIER_NONE,
                "reason": ladder.REASON_NOT_OFFERED,
                "serial": serial,
                "offer": True,
            }
        )
    return None, None


def _capture_declined_stage(serial: str) -> tuple:
    """``capture="off"``: remember the decline and record it at ``TIER_NONE``."""

    # Remember the decline, so the tester is not asked again on this
    # device. Recorded under the CURRENT fingerprint, but read back by
    # asked_before, which ignores the fingerprint on purpose: a new CA
    # must not reopen a closed conversation.
    _remember_capture_decline(serial)
    return None, ladder.record(
        {
            "tier": ladder.TIER_NONE,
            "reason": ladder.REASON_NO_CONSENT,
            "serial": serial,
        }
    )


@dataclass(frozen=True)
class _CaptureCall:
    """The five inputs of one capture stage."""

    serial: str
    apply: bool
    capture: str
    capture_ack: bool
    owner: str


# Notes for ``_mobile_capture_stage`` (moved out of the body to keep it short):
#     act on it. ``(markdown_or_none, capture_result_or_none)``.
#
#     Declining, or leaving `capture` unset, never stops the run -- the ONLY
#     markdown this returns is the consent MENU itself, when the tester has
#     said `capture="on"` but not yet confirmed with `capture_ack=true`. Every
#     other outcome, including any refusal reached while calling
#     ``mobile_capture.prepare``, is folded into `capture_result` at
#     `TIER_NONE` with its reason code, and the caller continues the run
#     (brief decision 2).
#
async def _mobile_capture_stage(call: _CaptureCall) -> tuple:
    """Between locale and install (T6.1): ask consent for API capture, or
    route on ``capture``: unset, declined, menu, or prepare.
    """

    wanted = str(call.capture or "").strip().lower()
    if wanted not in ("on", "off"):
        return _capture_unset_stage(call.serial)
    if wanted == "off":
        return _capture_declined_stage(call.serial)
    if not call.capture_ack:
        return mobile_render.capture_menu_markdown(), None
    return await _capture_prepare(call)


async def _capture_prepare(call: _CaptureCall) -> tuple:
    """Consent given: prepare the capture; a refusal folds into ``TIER_NONE``."""

    serial = call.serial
    prepared = await api_capture.prepare(serial, owner=call.owner, apply_=call.apply)
    if prepared.get("error"):
        reason = ladder.REASON_DEVICE_GONE
        return None, ladder.record(
            {"tier": ladder.TIER_NONE, "reason": reason, "serial": serial}
        )
    return None, prepared.get("content") or {}
