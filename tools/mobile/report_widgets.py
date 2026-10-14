"""Small HTML widgets shared by the report sections: pills, KPI tiles, section heads and the run wrapper."""

from __future__ import annotations

from tools.mobile.rwdg_args import _Chip, _Head, _Kpi, _PageData, _Run, _SecBlock
from tools.mobile.rwdg_basic import (
    MSEP,
    RAIL,
    _all_chip,
    _chip,
    _filter_group,
    _kpi,
    _metric,
    _outcome_pill,
    _pill,
    _sec_block,
    _sechead,
)
from tools.mobile.rwdg_seg import _segbar, _tile_order

__all__ = [
    "MSEP",
    "RAIL",
    "_Chip",
    "_Head",
    "_Kpi",
    "_PageData",
    "_Run",
    "_SecBlock",
    "_all_chip",
    "_chip",
    "_filter_group",
    "_kpi",
    "_metric",
    "_outcome_pill",
    "_pill",
    "_sec_block",
    "_sechead",
    "_segbar",
    "_tile_order",
]
