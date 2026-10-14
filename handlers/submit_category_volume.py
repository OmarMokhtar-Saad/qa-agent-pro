"""The generation-VOLUME gate, run on BOTH finalize routes."""

from __future__ import annotations

import logging

from agents.host_category_calls import VolumeGateIn
from agents.host_prep_volume import VolumeQuery
from config.settings_guard import never_raise
from handlers import _common, prep_status
from tools import prep_store

logger = logging.getLogger("tools.mcp_handlers")

_AUDITS = {
    "refuse": ("mcp_submit_suite_refused", "volume_floor"),
    "acked": ("mcp_submit_suite_volume_override", "volume_floor_ack"),
}


# TWO-BEAT ack: mark the prep refused so a LATER volume_floor_ack is
# honoured. Non-fatal: an unpersisted mark fails safe. 2026-08-12: the
# count is stamped so the NEXT refusal can say the resubmission shrank.
async def _mark_volume_refused(prep_id: str, envelope: dict, count: int) -> None:
    """Stamp the prep refused so a LATER volume_floor_ack is honoured."""
    meta = envelope.get("meta") or {}
    with never_raise(logger, "volume_refused mark failed", exc_info=True):
        _mark = await prep_store.update_prep(
            prep_id,
            {
                **envelope,
                "meta": {**meta, "volume_refused": True, "volume_last_count": count},
            },
        )
        if (_mark or {}).get("error"):
            logger.warning(
                "prep %s not marked volume_refused (%s)",
                prep_id,
                (_mark or {}).get("error"),
            )


async def _audit_volume(prep_id: str, mode: str, count: int) -> None:
    """Audit a refused or acknowledged volume floor."""
    event, reason = _AUDITS[mode]
    await _common.audit(
        event,
        entity_id=prep_id,
        detail={"reason": reason, "submitted_cases": count},
    )


async def volume_floor_gate(args: VolumeGateIn) -> tuple[str | None, str]:
    """The generation-VOLUME gate: ``(refusal, note)``; a refusal destroys nothing.

    Returns the refusal text (the caller prefixes its version note) or None to
    carry on, and the reply disclosure. Volume is measured pre-dedup."""
    prep_id, envelope, all_cases = args.prep_id, args.envelope, args.all_cases
    meta = envelope.get("meta") or {}
    mode, text = prep_status.prep_volume_floor_note(
        VolumeQuery(
            meta, all_cases, prep_id, ack=bool(args.ack), staged=args.staged_rows
        )
    )
    if mode == "refuse":
        await _mark_volume_refused(prep_id, envelope, len(all_cases))
        await _audit_volume(prep_id, mode, len(all_cases))
        return text, ""
    if mode == "acked":
        await _audit_volume(prep_id, mode, len(all_cases))
    return None, (text if mode else "")
