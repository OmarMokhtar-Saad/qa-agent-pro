"""Which learned steps answer a runtime-permission dialog (see ``kstg_flow_packets``).

A step records the button it tapped (or waited on), not the dialog's package, so a
step is a permission answer when it taps or waits on one of the permission-controller
buttons (``dialog_permission.PERMISSION_BUTTONS``). Idle waits belong to either side.
"""

from __future__ import annotations

from tools.mobile import dialog_permission

_OPS = frozenset({"tap", "tap_text_if_present", "wait_until_gone"})


def label(act: dict) -> str:
    """The visible text the step taps or waits on."""
    target = act.get("target")
    if isinstance(target, dict):
        return str(target.get("text") or target.get("label") or "")
    return str(act.get("text") or act.get("label") or target or "")


def kind(act: dict) -> str:
    """``"perm"`` for a permission answer, ``"other"``, or ``""`` for a neutral wait."""
    if act.get("op") in _OPS and dialog_permission.is_permission_button(label(act)):
        return "perm"
    return "" if str(act.get("op")).startswith("wait_") else "other"


def boundary(prev: str, act: dict) -> tuple:
    """``(cut, kind)``: whether *act* starts a new part after one of kind *prev*."""
    now = kind(act)
    return bool(prev and now and now != prev), (now or prev)
