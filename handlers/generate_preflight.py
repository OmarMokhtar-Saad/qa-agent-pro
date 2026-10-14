"""Host-mode prepare pre-flight: the re-sent-id probe and the open-prep image guard."""

from __future__ import annotations

from agents.host_gen_args import CarryArgs
from agents.host_gen_types import _PreflightCarry
from handlers import capture
from handlers.generate_args import PreflightArgs
from handlers.generate_host import host


def _carry_stamp(carry: _PreflightCarry, prep: dict) -> None:
    """Attribute ids revived by the probe to the open prep they came from."""
    if carry.carried_ids and not carry.carried_from:
        # Re-sent ids revived above: attribute them to the prep they came
        # from, so the stamp and the audit row say where the screens began.
        carry.carried_from = str(prep.get("prep_id") or "")


# RE-SENT ids first (2026-08-09): after a prepare ships them they sit on the
# carry-forward shelf, not in the tray. This is a PROBE (review M3): it
# reports what WOULD revive and mutates nothing, because a dismissible
# clarify can still end this call; the real revive is committed by the
# caller once the call is certain to proceed. The open-prep clarify itself
# is the caller's, because it is dismissible by `proceed_anyway`.
async def _prepare_preflight(
    args: PreflightArgs,
) -> tuple[str, _PreflightCarry, dict | None]:
    """``(refusal, carry, open_prep)``: the re-sent-id probe and the image half
    of the duplicate-prep guard. ``refusal`` is "" when the call may proceed.
    """
    ids, carried_ids, carry_note = capture.revive_resent_captures(args.capture_ids)
    carry = _PreflightCarry(ids, carried_ids, carry_note)
    have = host()._attested_image_count(args.attached_image_count, args.attached_images)
    prep = await host()._find_open_prep(args.text)
    if not prep:
        return "", carry, None
    refusal = host()._merge_carry_forward(
        CarryArgs(prep, carry, args.image_carry_ack, have)
    )
    if refusal:
        return refusal, carry, prep
    _carry_stamp(carry, prep)
    return "", carry, prep
