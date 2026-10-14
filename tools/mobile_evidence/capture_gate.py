"""The one place the operator-choice flag is read (plan D3).

``QA_MOBILE_APP_EVIDENCE`` is read HERE, before any adb call. The HTTPS-capture
kill-switch is not read here. A guard on a caller is only as good as the list of
callers, and the capture modules are importable from anywhere. Every reader
calls ``capture_gate.gate_refusal()`` at call time, so a patch of it bites all of them.
"""

from __future__ import annotations

from tools import settings_port
from tools.mobile_evidence import capture_consts as _consts


def gate_refusal() -> str | None:
    """The setting that says no, or None. The lane kill-switch is NOT read
    here: it gates provisioning and the certificate install only."""
    if not bool(getattr(settings_port.get(), "qa_mobile_app_evidence", True)):
        return (
            "Refusing to capture app evidence: `"
            + _consts.EVIDENCE_FLAG_NAME
            + "=false` in `.env` keeps the app's own log off this machine. No adb "
            "call was made and nothing was written."
        )
    return None


def _skipped(reason: str) -> dict:
    return {
        "error": None,
        "content": {
            "skipped": str(reason)[:200],
            "profile": None,
            "clock_offset_ms": None,
            "pid": None,
            "clock_note": None,
        },
    }
