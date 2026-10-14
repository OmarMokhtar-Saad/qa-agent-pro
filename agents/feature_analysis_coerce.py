"""Coercion of an untrusted host submission into a report (leaf of feature_analysis)."""

from __future__ import annotations

import logging

from agents.feature_analysis_model import _STR_FIELDS, FeatureAnalysisReport
from config.settings_guard import never_raise

logger = logging.getLogger("agents.feature_analysis")


def _coerce_field(name: str, value: object) -> str | list[str] | None:
    """One host-submitted field coerced to its type; ``None`` when nothing usable."""
    if name in _STR_FIELDS:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None
    if isinstance(value, str):
        value = [value]
    if isinstance(value, (list, tuple)):
        items = [str(i).strip() for i in value if str(i).strip()]
        return items or None
    return None


def _coerce_payload(payload: dict) -> dict:
    data: dict = {}
    for name in FeatureAnalysisReport.model_fields:
        if name not in payload:
            continue
        coerced = _coerce_field(name, payload[name])
        if coerced:
            data[name] = coerced
    return data


def _build_report(data: dict) -> tuple[FeatureAnalysisReport, bool]:
    with never_raise(
        logger,
        "finalize_feature_report: could not coerce the host submission",
        level=logging.WARNING,
        exc_info=True,
    ):
        return FeatureAnalysisReport(**data), True
    return FeatureAnalysisReport(), False


def finalize_feature_report(payload: object) -> tuple[FeatureAnalysisReport, bool]:
    """``(report, usable)`` from an UNTRUSTED host submission. Never raises.

    ``model_config = {"extra": "forbid"}`` would make a STRICT parse reject an
    otherwise perfect report just because the host volunteered one extra key, so
    unknown keys are DROPPED and known ones coerced field by field (a bare string
    where a list belongs becomes a one-item list; blanks are discarded).
    ``usable`` is False when nothing at all could be read -- the caller decides
    whether that earns a resubmit round. This never fabricates content to fill a
    section, which is the same rule ``_bullets`` follows when it renders
    ``_None identified._``.
    """
    if not isinstance(payload, dict):
        return FeatureAnalysisReport(), False
    data = _coerce_payload(payload)
    if not data:
        return FeatureAnalysisReport(), False
    return _build_report(data)
