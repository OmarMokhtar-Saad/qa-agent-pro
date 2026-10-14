"""Recognised review-sidecar fields and the sidecar retained on a prep."""

from __future__ import annotations

import logging
from typing import Any

from config.settings_guard import never_raise
from tools import atomic_checklist

logger = logging.getLogger("agents.host_mode")

_SIDECAR_KEYS = ("duplicate_groups", "acceptance_criteria", "ambiguity_result")

# What a sidecar-only submit may leave on the prep for a later finalize (F3).
# A CLOSED set, deliberately wider than _SIDECAR_KEYS because the two
# meta-gated fields are copied by the same merge block: an unbounded copy would
# let a host park arbitrary keys in the envelope.
_SIDECAR_RETAINABLE = (
    "duplicate_groups",
    "acceptance_criteria",
    "ambiguity_result",
    "checklist_items",
    "image_descriptions",
)
SIDECAR_RETAINABLE = _SIDECAR_RETAINABLE  # public alias (additive)

# [sidecar_keys]
# _SIDECAR_KEYS itself stays pinned by tests/test_host_ac_review.py.
# Never raises.
#
# Recognition is keyed off the PREP'S OWN meta stamp whenever ``meta`` is
# supplied -- never off live flag values, which is how the copy logic in
# handle_submit_suite already works, and which is what stops a flag flipped
# between prepare and submit from turning a legitimate sidecar into a
# confusing full-suite parse error. With no meta (module-level and legacy
# callers) the checklist falls back to the checklist_enabled() seam -- read
# rather than inlined so every checklist surface answers to the one switch
# tests/conftest.py pins.
#
# Phase 3a's two post_merge fold fields (`risk_scores` / `test_plan_report`)
# left this function on 2026-08-16 with the jobs themselves (dead-code
# deletion P2-H).
#
# The image job's return field is finalize-time review material like the
# others, so the staged (crash-safe) route must be able to carry it in a
# sidecar.
#
# Residue R4: the checklist job's return field is finalize-time material
# like the others, so the staged (crash-safe) Path A route must be able
# to carry it in a sidecar. Recognised from the prep's OWN meta stamp
# when meta is supplied (the Phase-3a pattern), with the live AND as the
# no-meta fallback. Deliberately NO id remap: CL-NNN ids are assigned
# server-side in extract_host_checklist and are never tc_ids, so 3a's
# _remap_risk_scores problem -- every staged category restarting at
# TC-001 -- cannot arise here.


def sidecar_keys(meta: object = None) -> tuple:
    """The recognised sidecar review fields for THIS request."""
    # pragma: no cover
    with never_raise(logger, "_sidecar_keys flag read failed", exc_info=True):
        keys: tuple[str, ...] = _SIDECAR_KEYS
        keys = keys + ("image_descriptions",)
        if _checklist_recognised(meta):
            keys = keys + ("checklist_items",)
        return keys
    return _SIDECAR_KEYS


def _checklist_recognised(meta: object) -> bool:
    """True when the checklist job's return field is a sidecar key."""
    if isinstance(meta, dict):
        return bool(meta.get("host_checklist_job"))
    return atomic_checklist.checklist_enabled()


# [retained_sidecar]
# Empty dict when there is none, when the envelope predates the retention
# LIFETIME -- the retention is never cleared, on purpose. Reviewed 2026-08-29:
# a second finalize that OMITS a key would re-apply the value the first one
# retained, and the present-but-empty discipline below covers "sent empty" but
# not "not sent at all". That is unreachable rather than merely unlikely: a
# successful finalize STAMPS the prep (``FINALIZED_KEY``, see the update_prep
# near the end of handle_submit_suite) rather than deleting it, and the next
# submit returns at the already-finalized guard before any sidecar work runs.
# The only route that finalizes twice on one prep is gap remediation, which is
# itself unreachable while ``checklist_remediation_enabled()`` is a ``False``
# constant -- and on THAT route re-applying is the behaviour you want: same
# prep, same ticket, so step-0 work should survive a remediation round rather
# than be silently dropped from the second suite. Clearing on fold would turn
# this into exactly the loss the retention exists to prevent.
#
# (an old prep simply has no `pending_sidecar` key) or when the stored value
# is not a dict. UNTRUSTED on the way back in: it is host-authored JSON that
# happens to have been round-tripped through our own store, so it is handed
# back into exactly the same `_apply`/`parse_host_suite` path a fresh
# submission takes and is re-validated there. Never raises.


def retained_sidecar(meta: Any) -> dict:
    """The review sidecar an EARLIER sidecar-only submit left on this prep."""
    with never_raise(logger, "_retained_sidecar read failed", exc_info=True):
        pending = (meta or {}).get("pending_sidecar")
        return dict(pending) if isinstance(pending, dict) else {}
    return {}
