"""Mobile session: submit audit detail, masked-field entries and screen_of. Cut verbatim from tools/mobile/session.py (P4.4)."""

from __future__ import annotations

import logging

from tools.mobile import (
    run_store,
)
from tools.mobile.providers import composite

logger = logging.getLogger("tools.mobile.session")


#: The most tester-supplied fields one submit may carry via ``tester_inputs``
#: (see :func:`_merge_tester_inputs`). A real login/registration turn asks for
#: a handful of fields -- a password, a one-time code -- never dozens; the cap
#: exists so an oversized multi-field payload cannot grow the audit's
#: ``tester_fields`` list, or the executor's per-field lookups, without bound.
MOBILE_MAX_TESTER_INPUTS = 8


def _masked_field_entries(fields: list[str] | None) -> list[dict]:
    """Marked, value-free entries for the tester field NAMES, capped."""
    names = [str(name)[:80] for name in fields or [] if str(name or "").strip()][
        :MOBILE_MAX_TESTER_INPUTS
    ]
    return [
        {"secret": True, "field": name, "value": run_store.SECRET_MASK}
        for name in names
    ]


def audit_detail(
    *,
    run_id: str = "",
    tc_id: str = "",
    verdict: str = "",
    status: str = "",
    field: str = "",
    fields: list[str] | None = None,
    step: str = "",
    apply: object = None,
) -> dict:
    """The ``detail`` dict an audit row may carry. **No value can reach it.**

    ``audit_log.record_event`` writes ``detail`` VERBATIM -- there is no
    redaction hook anywhere below this point -- and ``run_store.redact`` is
    mark-based plus a fixed list of credential-NAMED keys. A tester names their
    own field, so ``{"inputs": {"tenantToken": "..."}}`` would be written in
    clear. This function therefore has no parameter that could carry a value: it
    takes the field NAME and emits a MARKED entry, and the marker is what makes
    the ``redact`` pass below effective rather than incidental.
    """
    try:
        detail: dict = {
            "run_id": str(run_id or "")[:80],
            "tc_id": str(tc_id or "")[:16],
            "verdict": str(verdict or "")[:24],
            "status": str(status or "")[:32],
        }
        if step:
            detail["step"] = str(step)[:48]
        if apply is not None:
            detail["apply"] = bool(apply)
        if field:
            detail["tester_field"] = {
                "secret": True,
                "field": str(field)[:80],
                "value": run_store.SECRET_MASK,
            }
        masked = _masked_field_entries(fields)
        if masked:
            detail["tester_fields"] = masked
        return run_store.redact(detail)
    except Exception:  # pragma: no cover - defensive
        logger.warning("mobile.session.audit_detail failed", exc_info=True)
        return {}


def screen_of(dump: object, activity: str = "", display: object = None) -> dict:
    """Prune a dump. Here so a handler never touches raw XML itself.

    ``display`` is the device's natural size where the caller has it. Without it
    `prune` derives the frame from the dump's own windows, which over-estimates
    rather than under-estimates and so cannot lose the screen -- see
    `perception._display_frame`.

    NO PRODUCTION CALLER TODAY, and one thing to know before writing the first
    one: ``activity`` defaults to ``""``, and a screen pruned without an activity
    gets a screen_id hashed on two dimensions instead of three -- a DIFFERENT id
    from the one `executor` stamps on every trace entry. If a screen from here is
    ever stored in a run, pass ``await executor.resolve_activity(ctx)``, or the
    report's join silently loses every picture (measured; see
    ``tests/mobile/test_mobile_screen_id_producer``). This function takes no run
    id, so that module's class pin cannot see it.
    """
    return composite.observe(dump, activity, display=display)
