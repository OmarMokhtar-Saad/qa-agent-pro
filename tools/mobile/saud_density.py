"""The density-aware pieces of the screen audit: empty shape, floor, target wording."""

from __future__ import annotations

from typing import Any

from tools.mobile.saud_consts import (
    ASSUMED_DENSITY_CAVEAT,
    ASSUMED_DENSITY_DPI,
    DENSITY_ASSUMED,
    DENSITY_BASELINE_DPI,
    DENSITY_DEVICE,
    MIN_TOUCH_TARGET_DP,
    MIN_TOUCH_TARGET_PX,
    NO_NODES_NOTE,
)


def _empty(present: bool) -> dict:
    """The shape every return of :func:`audit` has, with nothing found."""
    return {
        "present": bool(present),
        "auditable": False,
        "complete": False,
        "audited": 0,
        "dropped": 0,
        "truncated": False,
        "density_dpi": int(ASSUMED_DENSITY_DPI),
        "density_scale": float(ASSUMED_DENSITY_DPI) / float(DENSITY_BASELINE_DPI),
        "density_source": DENSITY_ASSUMED,
        "min_target_px": int(MIN_TOUCH_TARGET_PX),
        "findings": [],
        "capped": 0,
        "note": NO_NODES_NOTE if present else "",
    }


def _small_side(element: dict, floor: int) -> str:
    box = element.get("bounds") or []
    if not (isinstance(box, (list, tuple)) and len(box) == 4):
        return ""
    try:
        width = int(box[2]) - int(box[0])
        height = int(box[3]) - int(box[1])
    except (TypeError, ValueError, OverflowError):
        return ""
    if width <= 0 or height <= 0:
        return ""
    if width >= floor and height >= floor:
        return ""
    return str(width) + "x" + str(height) + "px"


def _target_head(side: str, floor: int) -> str:
    return (
        "this target is "
        + side
        + ", under the "
        + str(floor)
        + "px that "
        + str(MIN_TOUCH_TARGET_DP)
        + "dp is at "
    )


def _target_detail(side: str, floor: int, result: dict) -> str:
    """The touch-target finding's words. TWO wordings, never one with a caveat.

    A measured density states a fact. An assumed one states an assumption AND
    its failure direction, because the reader's next action differs: file it, or
    check the device first. The same rule the screenshot notes follow.
    """
    scale = "%.2gx" % float(result.get("density_scale") or 0)
    head = _target_head(side, floor)
    dpi = str(result.get("density_dpi"))
    if result.get("density_source") == DENSITY_DEVICE:
        return head + "this device's measured density of " + dpi + "dpi (" + scale + ")"
    return head + "an assumed " + dpi + "dpi (" + scale + ")" + ASSUMED_DENSITY_CAVEAT


def _apply_density(result: dict, density_dpi: Any) -> None:
    """Write the device-measured density into ``result`` when it is plausible."""
    try:
        dpi = int(density_dpi) if density_dpi else 0
        measured = dpi > 0 and (
            DENSITY_BASELINE_DPI // 4 <= dpi <= DENSITY_BASELINE_DPI * 8
        )
        scale = dpi / DENSITY_BASELINE_DPI if measured else 0.0
        measured_floor = (
            MIN_TOUCH_TARGET_DP * dpi // DENSITY_BASELINE_DPI if measured else 0
        )
    except (TypeError, ValueError, OverflowError, ZeroDivisionError):
        dpi, measured, scale, measured_floor = 0, False, 0.0, 0
    if measured:
        result["density_dpi"] = dpi
        result["density_scale"] = scale
        result["density_source"] = DENSITY_DEVICE
        result["min_target_px"] = measured_floor
