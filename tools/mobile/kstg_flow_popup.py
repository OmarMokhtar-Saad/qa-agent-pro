"""Permission-popup taps are learned as optional steps (see ``kstg_flow_build``).

A tap on an OS permission dialog (``Allow`` / ``Not Allow``) ran on a screen of another
package, so the route used to drop it. Whether the popup shows depends on the install,
so the step is kept as ``tap_text_if_present``: it taps when the popup is there and
costs nothing when it is not. Pure; never raises.
"""

from __future__ import annotations

from tools.mobile import dialog_permission, kstg_flow_perm, kstg_foreign
from tools.mobile.kstg_popup_permission import is_permission_decline

POPUP_WAIT_S = 3.0
_TAPS = ("tap", "tap_text", "tap_text_if_present")


def is_popup_tap(rec: dict, package: str) -> bool:
    """Whether *rec* answered a permission dialog of another package."""
    act = rec.get("flow_action") or {}
    text = kstg_flow_perm.label(act)
    named = dialog_permission.is_permission_button(text) or is_permission_decline(text)
    return act.get("op") in _TAPS and named and not kstg_foreign.own(rec, package)


def as_optional(rec: dict, package: str) -> dict:
    """*rec* with a popup tap turned into an optional step on the app's own record.

    Answering the popup leaves the app screen as it was, so the step's after key is its
    before key: a route of only such steps stays inert (``flow_inert``)."""
    if not is_popup_tap(rec, package):
        return rec
    text = kstg_flow_perm.label(rec["flow_action"])
    act = {"op": "tap_text_if_present", "text": text, "max_s": POPUP_WAIT_S}
    still = rec.get("screen_key")
    return dict(
        rec,
        flow_action=act,
        screen_package=package,
        activity="",
        after_screen_key=still,
    )


def _text_tap(act: dict) -> bool:
    return act.get("op") in _TAPS and bool(kstg_flow_perm.label(act))


def _came_and_went(prev: dict, rec: dict) -> bool:
    """*rec* tapped away a screen that *prev* opened, back to where *prev* began."""
    start, opened = prev.get("screen_key"), prev.get("after_screen_key")
    if not start or not opened or start == opened:
        return False
    return rec.get("screen_key") == opened and rec.get("after_screen_key") == start


def as_inapp_optional(prev: dict, rec: dict) -> dict:
    """*rec* as an optional step when it only dismissed a sheet the step *prev* opened.

    Whether an in-app popup shows depends on the install and the day, so replay must
    tap it when it is there and go on when it is not. Judged by the screen keys alone.
    """
    act = rec.get("flow_action") or {}
    if not _text_tap(act) or not _came_and_went(prev, rec):
        return rec
    text = kstg_flow_perm.label(act)
    keep = {"op": "tap_text_if_present", "text": text, "max_s": POPUP_WAIT_S}
    return dict(rec, flow_action=keep)
