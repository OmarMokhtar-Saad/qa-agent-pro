"""Host mode: JSON-native dict -> PreparedGeneration (deserialize side of the prep record)."""

from __future__ import annotations

from agents.host_prep_serde_consts import _SCHEMA_VERSION
from agents.host_prep_serde_errors import PrepDeserializeError
from agents.host_prep_serde_rules import _deserialize_rule_packs
from agents.test_scenario_agent import PreparedGeneration
from tools.atomic_checklist import ChecklistItem
from tools.rtm import AcceptanceCriterion


# Inverse of ``serialize_prepared`` -> a REAL PreparedGeneration that
# ``_finalize_generation`` consumes unchanged.
#
# Rejects (PrepDeserializeError) a non-dict, a missing/unknown schema version,
# or any structurally invalid field. Constructs ONLY fixed known classes -- no
# eval, no __import__, no payload-driven class lookup -- because the payload is
# UNTRUSTED on the way back in (it round-trips through SQLite and ops-3d treats
# host input as untrusted).
def deserialize_prepared(payload: dict):
    _check_prep_envelope(payload)
    try:
        return PreparedGeneration(**_prepared_fields(payload))
    except PrepDeserializeError:
        raise
    except Exception as exc:  # KeyError / TypeError / ValidationError from tampering
        raise PrepDeserializeError(f"malformed prep payload: {exc}") from exc


def _check_prep_envelope(payload) -> None:
    """Reject a non-dict payload or an unsupported schema version."""
    if not isinstance(payload, dict):
        raise PrepDeserializeError("prep payload is not an object")
    if payload.get("_v") != _SCHEMA_VERSION:
        raise PrepDeserializeError(
            f"unsupported prep schema version {payload.get('_v')!r} "
            f"(this build reads v{_SCHEMA_VERSION})"
        )


def _structured_fields(payload: dict) -> dict:
    acs = [AcceptanceCriterion(**a) for a in payload.get("acs") or []]
    source_acs = [AcceptanceCriterion(**a) for a in payload.get("source_acs") or []]
    checklist_items = [
        ChecklistItem(**it) for it in payload.get("checklist_items") or []
    ]
    rule_packs = _deserialize_rule_packs(payload.get("rule_packs") or {})
    categories = [tuple(t) for t in payload.get("categories") or []]
    return dict(
        acs=acs,
        source_acs=source_acs,
        checklist_items=checklist_items,
        rule_packs=rule_packs,
        categories=categories,
    )


# .get, not [...]: a prep record written before this field existed
# must still load rather than failing the whole boomerang.
def _scalar_fields(payload: dict) -> dict:
    return dict(
        user_msg=str(payload["user_msg"]),
        rtm_hint=str(payload["rtm_hint"]),
        feature_text=str(payload["feature_text"]),
        complexity_text=str(payload["complexity_text"]),
        checklist_presented_ids=list(payload.get("checklist_presented_ids") or []),
        checklist_audit=dict(payload.get("checklist_audit") or {}),
        ui_content=payload.get("ui_content"),
        parent_context=str(payload["parent_context"]),
        cache_prefix_warm=bool(payload.get("cache_prefix_warm", False)),
        jira_image_text=str(payload["jira_image_text"]),
        attached_image_text=str(payload["attached_image_text"]),
        jira_context_text=str(payload["jira_context_text"]),
        image_notice=str(payload["image_notice"]),
        category_response_schema=dict(payload.get("category_response_schema") or {}),
        target_description=str(payload.get("target_description", "") or ""),
    )


# The keyword arguments for ``PreparedGeneration``, built from an
# untrusted payload. May raise KeyError / TypeError on tampering.
def _prepared_fields(payload: dict) -> dict:
    fields = _structured_fields(payload)
    fields.update(_scalar_fields(payload))
    return fields
