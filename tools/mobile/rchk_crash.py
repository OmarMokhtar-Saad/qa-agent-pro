"""The crash-disclosure and capture-tier pins of the report self-check."""

from __future__ import annotations

from typing import Any

from tools.mobile.rchk_consts import PIN_CAPTURE_TIER, PIN_CRASH
from tools.mobile.rchk_pins import _pin
from tools.mobile_capture import ladder
from tools.mobile_evidence import crash_detector


def _store_crashes(cases: Any) -> dict:

    store = {}
    for case in list(cases or []):
        crash = crash_detector.crash_of_case(case)
        if crash:
            store[str((case or {}).get("tc_id") or "")] = str(crash.get("kind") or "")
    return store


# Every crash the STORE recorded is disclosed on the page, and no other.
#
# An EQUALITY, per this module's own discipline: a page that dropped the
# disclosure fails, and so does one that invented a crash the store never
# recorded. The store side goes through ``crash_detector.crash_of_case``, so
# this pin cannot disagree with the renderer about where a crash lives.
def _pin_crashes(page_crashes: Any, cases: Any) -> dict:
    """Every crash the store recorded is on the page, and no other."""
    page = {
        str(tc): str(kind)
        for tc, kind in (page_crashes or {}).items()
        if str(kind or "")
    }
    store = _store_crashes(cases)
    missing = sorted(tc for tc in store if page.get(tc) != store[tc])
    invented = sorted(tc for tc in page if tc not in store)
    return _pin(
        PIN_CRASH,
        not missing and not invented,
        "store="
        + str(len(store))
        + " page="
        + str(len(page))
        + (" undisclosed=" + ",".join(missing[:8]) if missing else "")
        + (" unexpected=" + ",".join(invented[:8]) if invented else ""),
    )


def _pin_capture_tier(manifest: dict) -> dict:
    """A manifest carrying a ``capture`` block must state a tier, and that
    tier must be one of ``tools.mobile_capture.ladder.TIERS`` (T7.4).

    A manifest with NO ``capture`` key at all -- a run planned before this
    feature -- has nothing to state, so this pin is inert on it rather than a
    false failure: absence and invalidity are two different findings.
    """

    capture = manifest.get("capture") if isinstance(manifest, dict) else None
    if not isinstance(capture, dict):
        return _pin(PIN_CAPTURE_TIER, True, "no capture block on this manifest")
    tier = capture.get("tier")
    ok = tier in ladder.TIERS
    return _pin(
        PIN_CAPTURE_TIER, ok, "tier=" + repr(tier) if not ok else "tier=" + str(tier)
    )
