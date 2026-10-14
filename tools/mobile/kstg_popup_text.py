"""Popup sightings by label: a sheet dismissed with "No, Later" / "Not Allow".

The dismiss tap leaves the sheet's screen for another one, and its label (or rid)
says "not now". The sheet's own screen key and that label are what the popup plug
matches on the next run. Labels are UI strings; no typed value enters a slot.
"""

from __future__ import annotations

from tools.mobile import kplg_popup_ref

_TAPS = ("tap", "tap_text")


def _degraded(key: str) -> bool:
    return not key or key.startswith("sk1d:")


def _moved(rec: dict) -> bool:
    key = str(rec.get("screen_key") or "")
    after = str(rec.get("after_screen_key") or "")
    return not (_degraded(key) or _degraded(after) or after == key)


def slot(rec: dict) -> tuple | None:
    """``(sheet_key, activity, ref)`` when *rec* dismissed a sheet and the screen moved on."""
    label = str(rec.get("target_text") or "")
    if rec.get("op") not in _TAPS or rec.get("outcome") != "ok":
        return None
    if not kplg_popup_ref.is_dismiss_label(label) or not _moved(rec):
        return None
    ref = str(rec.get("target_rid") or "") or kplg_popup_ref.text_ref(label)
    return (str(rec["screen_key"]), str(rec.get("activity") or ""), ref)


def slots(steps) -> dict:
    """``{slot: times}`` for every label-dismissed sheet in *steps*."""
    found: dict = {}
    for rec in steps or []:
        got = slot(rec)
        if got is not None:
            found[got] = found.get(got, 0) + 1
    return found
