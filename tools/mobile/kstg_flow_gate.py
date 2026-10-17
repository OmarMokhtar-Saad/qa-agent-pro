"""The save gate for a learned flow (see ``knowledge_stage_flows``).

A route is refused, with the reason, when replaying it would only mislead: it ends in a
tap that names no target, it mixes screen-key families (a degraded ``sk1d`` key beside
a full ``sk2`` one: two different notions of "the same screen"), or it repeats a tap
that changed nothing (a no-op loop). The goal check itself is ``kstg_flow_build.reached``
and ``ended_clean``, run before this. Pure; never raises.
"""

from __future__ import annotations

import re

from tools.mobile.kstg_foreign import observed
from tools.mobile.kstg_popup_permission import is_permission_decline

_FAMILY_RE = re.compile(r"^(sk[0-9]+[a-z]*):")
_TAPS = ("tap", "tap_text", "tap_text_if_present")
_SELECTORS = ("rid", "text", "label", "role")


def _names_target(act: dict) -> bool:
    got = act.get("target")
    target: dict = got if isinstance(got, dict) else {}
    return any(target.get(k) for k in _SELECTORS) or bool(
        act.get("text") or act.get("label")
    )


def _empty_tap_end(recs: list) -> bool:
    act = (recs[-1].get("flow_action") or {}) if recs else {}
    return act.get("op") in _TAPS and not _names_target(act)


def family(key: object) -> str:
    """``sk2`` / ``sk1d`` of a versioned key; ``""`` for any other (unversioned) key."""
    found = _FAMILY_RE.match(str(key or ""))
    return found.group(1) if found else ""


def mixed_keys(recs: list) -> bool:
    keys = [r.get(w) for r in recs for w in ("screen_key", "after_screen_key")]
    return len({f for f in map(family, keys) if f}) > 1


def _still(rec: dict) -> bool:
    before, after = rec.get("screen_key"), rec.get("after_screen_key")
    return bool(before) and before == after


def _same(a: dict, b: dict) -> bool:
    return (a.get("flow_action") or {}) == (b.get("flow_action") or {})


def _noop_loop(recs: list) -> bool:
    """True when a tap that left the screen unchanged is immediately repeated."""
    for prev, cur in zip(recs, recs[1:]):
        op = (cur.get("flow_action") or {}).get("op")
        if op in _TAPS and _still(prev) and _same(prev, cur):
            return True
    return False


def declines(rec: dict) -> bool:
    """True when the step taps a label that declines a runtime permission."""
    act = rec.get("flow_action") or {}
    got = act.get("target")
    target: dict = got if isinstance(got, dict) else {}
    labels = (
        act.get("text"),
        act.get("label"),
        target.get("text"),
        target.get("label"),
    )
    return act.get("op") in _TAPS and any(map(is_permission_decline, labels))


def foreign_decline(rec: dict, package: str) -> bool:
    """A decline on a screen not proven to be *package*'s own (an OS permission dialog).

    Replay allows OS permission dialogs, so a flow must not decline one. A decline on
    the app's own sheet is part of the app's route and replays as recorded.
    """
    return declines(rec) and (not package or observed(rec) != package)


def _declines_permission(recs: list, package: str) -> bool:
    """True when a tap declines a permission outside the app's own screens."""
    return any(foreign_decline(rec, package) for rec in recs)


def reject(recs: list, package: str = "") -> str:
    """Why *recs* must not be saved as a flow, or ``""`` when they pass."""
    if _empty_tap_end(recs):
        return "ends in a tap that names no target"
    if mixed_keys(recs):
        return "mixes screen-key families"
    if _noop_loop(recs):
        return "repeats a tap that changed nothing"
    if _declines_permission(recs, package):
        return "taps a runtime-permission decline"
    return ""
