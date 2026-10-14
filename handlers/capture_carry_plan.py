"""The arithmetic of one carry-forward decision: ``_CarryPlan`` and ``_carry_plan``."""

from __future__ import annotations

from typing import NamedTuple

from handlers import image_gate
from handlers.capture_evict import _dedup_ids
from handlers.capture_revive import (  # noqa: F401
    resolvable_captures,
    revive_captures,
)


class _CarryPlan(NamedTuple):
    """The arithmetic of one carry-forward decision, before any wording.

    UNITS (2026-08-09): `prior_shipped_cap` is the prior prep's SHIPPED captured
    count (post jira_max_images / byte budget) while `prior_ids` is EVERY id
    that prep was CALLED with, pre-budget. Conflating them was review H1.

    PER-CHANNEL (review H1): each channel is compared against what THIS call
    actually RESOLVED, not against a raw "does this call carry images?" boolean.

    SURPLUS comes from the OBSERVED (captured) channel only (review C1, C1-R):
    an attached_image_count is an unverifiable host assertion and must never
    cancel a captured-channel loss. It is the SMALLER of "resolved screens the
    prior prep did not have" and "resolved beyond what that prep shipped", so a
    new screen may close the gap or pay a surplus, never both. Surplus is
    applied AFTER recovery is attempted. Residual (W6): a captured surplus can
    net out an expired-id shortfall silently -- the substitution semantics. A
    prep row with a captured count but NO `capture_ids` credits by count alone.

    resolvable_captures fails toward the disclosure (its except reports
    nothing resolvable): the cost is one dismissible refusal.
    """

    from_prep: str
    age_s: float
    merged_ids: list
    revived_ids: list
    resolved_cap: int
    resolved_att: int
    prior_shipped_cap: int
    prior_att: int
    gap_cap: int
    gap_att: int
    recovered_cap: int
    surplus_cap: int
    short_total: int
    identity_cap: int


def _carry_age(prep: dict) -> float:
    try:
        return float(prep.get("age_s") or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _split_resolvable(prep: dict, capture_ids: list | None) -> tuple:
    """``(call_ids, prior_ids, resolved_ids)`` for one carry decision."""
    call_ids = _dedup_ids(capture_ids)
    prior_ids = _dedup_ids(prep.get("capture_ids"))
    return call_ids, prior_ids, resolvable_captures(call_ids)


def _revive_for_plan(call_ids: list, prior_ids: list, gap_cap: int) -> list:
    """The ids the shelf gives back to close the captured gap."""
    # Only ids this call does NOT already hold can close the captured gap:
    # counting a screen it already resolved would "recover" it twice.
    wanted_ids = [c for c in prior_ids if c not in call_ids]
    return revive_captures(wanted_ids) if (wanted_ids and gap_cap) else []


def _gap_fields(prior: tuple, resolved_ids: list, have_attested: int) -> dict:
    """The per-channel counts and gaps (``prior`` = shipped, attested)."""
    prior_cap, prior_att = prior
    resolved_att = image_gate._clamped_count(have_attested)
    return {
        "prior_shipped_cap": prior_cap,
        "prior_att": prior_att,
        "resolved_cap": len(resolved_ids),
        "resolved_att": resolved_att,
        "gap_cap": max(0, prior_cap - len(resolved_ids)),
        "gap_att": max(0, prior_att - resolved_att),
    }


# MERGED, never replaced -- this call's own ids (including unknown ones,
# which must survive to be DISCLOSED through _cap_missing) come first.
# IDENTITY, not arithmetic residual (2026-08-10): only a revived shelf
# screen or a re-sent id the prior prep was itself called with counts
# toward "the previous preparation had"; a surplus is its own clause.
def _recovery_fields(ids: tuple, gaps: dict) -> dict:
    """The surplus, recovery and merged-id fields; revives from the shelf."""
    call_ids, prior_ids, resolved_ids = ids
    new_cap = len([c for c in resolved_ids if c not in prior_ids])
    surplus = min(new_cap, max(0, gaps["resolved_cap"] - gaps["prior_shipped_cap"]))
    revived_ids = _revive_for_plan(call_ids, prior_ids, gaps["gap_cap"])
    recovered = min(len(revived_ids), gaps["gap_cap"])
    merged_ids = call_ids + [c for c in revived_ids if c not in call_ids]
    return {
        "merged_ids": merged_ids,
        "revived_ids": revived_ids,
        "recovered_cap": recovered,
        "surplus_cap": surplus,
        "short_total": _short_total(gaps, recovered, surplus),
        "identity_cap": recovered + len([c for c in resolved_ids if c in prior_ids]),
    }


def _short_total(gaps: dict, recovered: int, surplus: int) -> int:
    return max(0, gaps["gap_cap"] + gaps["gap_att"] - recovered - surplus)


def _carry_plan(
    prep: dict | None, capture_ids: list | None, have_attested: int
) -> "_CarryPlan | None":
    """The per-channel gap, surplus and recovery arithmetic (rules: see
    ``_CarryPlan``); ``None`` when the prior prep grounded on no screens at all.
    May raise: the caller fails open."""
    _prep = prep or {}
    prior_cap = image_gate._clamped_count(_prep.get("captured_image_count"))
    prior_att = image_gate._clamped_count(_prep.get("attached_image_count"))
    if prior_cap + prior_att <= 0:
        return None
    ids = _split_resolvable(_prep, capture_ids)
    fields = _gap_fields((prior_cap, prior_att), ids[2], have_attested)
    fields.update(_recovery_fields(ids, fields))
    return _CarryPlan(
        from_prep=str(_prep.get("prep_id") or ""), age_s=_carry_age(_prep), **fields
    )
