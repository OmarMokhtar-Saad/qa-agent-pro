"""Settings-clamped limits and the keeper ordering of the host duplicate review."""

from __future__ import annotations

from agents.host_dedup_consts import (
    _DUP_LOW_TEXT_DEFAULT,
    _DUP_MAX_GROUP_SIZE,
    _DUP_MAX_GROUPS,
    _DUP_PRIORITY_RANK,
    _DUP_REMOVAL_RATIO_CEILING,
    _DUP_REMOVAL_RATIO_DEFAULT,
)
from tools import settings_port


def _dup_keeper_key(pair) -> tuple:
    """Sort key picking a group's keeper: highest declared priority first, ties
    broken by the earliest position in the submission. Deterministic and pure."""
    idx, tc = pair
    pri = getattr(getattr(tc, "priority", None), "value", "") or ""
    return (_DUP_PRIORITY_RANK.get(pri, 99), idx)


def _removal_ratio() -> float:
    """Max share of the SUBMITTED cases one host review may remove. An operator may
    LOWER this; the module CEILING wins, so it can never be raised."""
    try:
        cfg = float(
            getattr(
                settings_port.get(),
                "qa_host_dedup_max_removal_ratio",
                _DUP_REMOVAL_RATIO_DEFAULT,
            )
        )
    except (TypeError, ValueError, OverflowError):
        cfg = _DUP_REMOVAL_RATIO_DEFAULT
    return max(0.0, min(_DUP_REMOVAL_RATIO_CEILING, cfg))


def _low_text_ratio() -> float:
    """Threshold below which a group is LABELLED low-agreement in the report. Purely
    presentational -- it gates nothing (see ``dup_agreements``)."""
    try:
        cfg = float(
            getattr(
                settings_port.get(),
                "qa_host_dedup_low_text_ratio",
                _DUP_LOW_TEXT_DEFAULT,
            )
        )
    except (TypeError, ValueError, OverflowError):
        cfg = _DUP_LOW_TEXT_DEFAULT
    return max(0.0, min(1.0, cfg))


def _int_setting(name: str, default: int) -> int:
    """Integer setting *name*, or *default* when unset or not an integer."""
    try:
        return int(getattr(settings_port.get(), name, default) or default)
    except (TypeError, ValueError, OverflowError):
        return default


def _dup_group_caps() -> tuple[int, int]:
    """``(max_groups, max_size)`` from settings, clamped to the hard ceilings."""
    cfg_groups = _int_setting("qa_host_dedup_max_groups", _DUP_MAX_GROUPS)
    cfg_size = _int_setting("qa_host_dedup_max_group_size", _DUP_MAX_GROUP_SIZE)
    return (
        min(_DUP_MAX_GROUPS, max(1, cfg_groups)),
        min(_DUP_MAX_GROUP_SIZE, max(2, cfg_size)),
    )
