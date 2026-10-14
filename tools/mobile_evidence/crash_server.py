"""Crash detector server side: the case's crash record and the override reason."""

from __future__ import annotations

import logging

from tools.mobile_evidence.crash_const import MAX_MARKER_CHARS, SERVER_PREFIX

logger = logging.getLogger("tools.mobile_evidence.crash_detector")


def crash_of_case(case: object) -> dict:
    """The crash record on a CASE checkpoint, or ``{}``. Never raises.

    THE one place that knows where a crash lives -- ``case["evidence"]["crash"]``
    -- and that a record is only a crash when ``detected``. The report and the
    chat both call this instead of walking the record themselves: two walks are
    two derivations of the same answer, and mirrored conditions drift.
    """
    try:
        body = case if isinstance(case, dict) else {}
        evidence = body.get("evidence")
        evidence = evidence if isinstance(evidence, dict) else {}
        crash = evidence.get("crash")
        if isinstance(crash, dict) and crash.get("detected"):
            return crash
        return {}
    except Exception:  # pragma: no cover - defensive
        logger.exception("mobile_evidence.crash_detector.crash_of_case failed")
        return {}


def _compose(label: str, marker: str, mine: str) -> str:
    """The override sentence: server attribution first, the model's words last."""
    text = (
        SERVER_PREFIX
        + " the app under test crashed during this case ("
        + label
        + "), so a pass cannot stand. This is the server's call, not your"
        " own judgement, and the run's own logcat is the proof."
    )
    if marker:
        text += " The line that proves it: " + marker
    if mine:
        text += ' The model reported: "' + mine + '"'
    return text


# THE override reason. One producer, and it never impersonates the model.
#
# The server's own attribution is the FIRST clause, because
# ``render.verdict_line`` clips a reason to 160 characters -- a sentence that
# explains itself after the clip explains nothing. The model's own words are
# quoted, labelled and LAST, so the tester can see both judgements and whose
# each one is.
def server_reason(crash: object, model_reason: object = "") -> str:
    """THE override reason. One producer, and it never impersonates the model."""
    try:
        body = crash if isinstance(crash, dict) else {}
        label = str(body.get("label") or "the app under test died")[:120]
        marker = str(body.get("marker") or "")[:MAX_MARKER_CHARS]
        mine = str(model_reason or "").strip()[:200]
        return _compose(label, marker, mine)
    except Exception:  # pragma: no cover - defensive
        logger.exception("mobile_evidence.crash_detector.server_reason failed")
        return SERVER_PREFIX + " the app under test crashed during this case."
