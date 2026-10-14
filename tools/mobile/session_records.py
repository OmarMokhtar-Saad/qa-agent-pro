"""Mobile session: locale and run records (device, capture, reset-app). Cut verbatim from tools/mobile/session.py (P4.4).

Facade over ``session_records_locale`` and ``session_records_device``: every old
name is re-exported here.
"""

from __future__ import annotations

import logging
from typing import Any

from tools.mobile import (  # noqa: F401
    adb,
)
from tools.mobile.session_core import STATE_ABANDONED
from tools.mobile.session_records_device import (  # noqa: F401
    _device_detail,
    _device_text,
    device_line,
    device_record,
)
from tools.mobile.session_records_locale import (  # noqa: F401
    LOCALE_FROM_BOOT,
    LOCALE_FROM_DEVICE,
    LOCALE_FROM_SETPROP,
    LocaleFacts,
    _locale_body,
    _locale_changed,
    _locale_confirm,
    _locale_content,
    _locale_source,
    _LocaleCase,
    _same_language,
    apply_locale,
    locale_phrase,
    locale_record,
)
from tools.mobile.session_verdict_explore import explore_verdict
from tools.mobile.verdicts import (
    VERDICT_FAIL,
    VERDICT_PASS,
    VERDICT_UNVERIFIED,
)
from tools.mobile_capture import ladder

logger = logging.getLogger("tools.mobile.session")


def capture_record(source: object) -> dict:
    """Normalise the run-level API-capture tier for the manifest. Never raises.

    THE ONE PRODUCER of the tier vocabulary is ``tools.mobile_capture.ladder``;
    this is a thin pass-through so the two planners below need no import at
    module load time and every consumer of the manifest's ``capture`` key
    reads the SAME shape ``ladder.record`` defines -- never a second
    normaliser drifting from the first.
    """

    return ladder.record(source)


def reset_app_record(body: object) -> dict:
    """Normalise the pre-run app-reset outcome for the manifest. Never raises.

    The counterpart of :func:`locale_record`/:func:`capture_record`: one
    shape written by both planners below and read by every consumer, so a
    finished run still states whether its own app's data was wiped before it
    started, after the device that ran ``pm clear`` is gone.
    """
    given = body if isinstance(body, dict) else {}
    return {
        "requested": bool(given.get("requested")),
        "package": str(given.get("package") or ""),
        "cleared": bool(given.get("cleared")),
    }


def _clear_ops(cases: Any) -> tuple[bool, bool]:
    """(requested, done) from every ``clear_app_data`` op in the case traces."""
    requested = done = False
    for row in list(cases or []):
        if not isinstance(row, dict):
            continue
        for item in list(row.get("trace") or []):
            if not isinstance(item, dict):
                continue
            action: Any = (
                item.get("action") if isinstance(item.get("action"), dict) else {}
            )
            if str(action.get("op") or "") != "clear_app_data":
                continue
            requested = True
            if str(item.get("outcome") or "") == "ok":
                done = True
    return requested, done


# What "reset" means for the relay block: requested from EITHER the
# launch-time ``reset_app=true`` flag (the manifest's ``reset_app`` record,
# see :func:`reset_app_record`) OR a ``clear_app_data`` op the model
# submitted mid-run, and done only when a clear actually landed. A
# requested reset that never confirmed is exactly the gap the relay block
# exists to surface. A run once read as a pass after a reset that never ran.
def reset_step_status(resolved: object, cases: Any) -> dict:
    """What "reset" means for the relay block (see the comment above)."""
    body = resolved if isinstance(resolved, dict) else {}
    manifest_reset = body.get("reset_app")
    manifest_reset = manifest_reset if isinstance(manifest_reset, dict) else {}
    ops_requested, ops_done = _clear_ops(cases)
    return {
        "requested": bool(manifest_reset.get("requested")) or ops_requested,
        "done": bool(manifest_reset.get("cleared")) or ops_done,
    }


def _verdict_of(rows: list) -> str:
    """pass / fail / unverified from the per-case verdicts alone."""
    if not rows:
        return VERDICT_UNVERIFIED
    verdicts = {str(row.get("verdict") or "") for row in rows}
    if VERDICT_FAIL in verdicts:
        return VERDICT_FAIL
    if verdicts <= {VERDICT_PASS}:
        return VERDICT_PASS
    return VERDICT_UNVERIFIED


# pass / fail / unverified -- the ONE word a tester reads to answer "did
# this run pass". An abandoned run and a requested-but-unconfirmed reset
# BOTH force ``unverified``: a run this lane cannot vouch for must never
# read as a pass, which is the defect a chat once told a tester "Login
# succeeded" against.
def run_verdict(resolved: object, cases: Any) -> str:
    """pass / fail / unverified -- the ONE word a tester reads (see above)."""
    body = resolved if isinstance(resolved, dict) else {}
    rows = [row for row in list(cases or []) if isinstance(row, dict)]
    reset = reset_step_status(body, rows)
    if str(body.get("state") or "") == STATE_ABANDONED:
        return VERDICT_UNVERIFIED
    if reset.get("requested") and not reset.get("done"):
        return VERDICT_UNVERIFIED
    if body.get("explore") and explore_verdict(rows):
        return VERDICT_PASS
    return _verdict_of(rows)
