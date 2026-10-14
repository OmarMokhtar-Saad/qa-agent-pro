"""Capture-lane render pieces: limits note, tier labels, flow rows, totals."""

from __future__ import annotations

from typing import Any

from tools.mobile_evidence.render_esc import MAX_REPLY_TEXT, ev_esc
from tools.mobile_shared import capture_tiers

#: Coverage limits every capture-tier report states, at EVERY tier -- never
#: only on failure. QUIC/HTTP3 over UDP:443 bypasses this lane's TCP proxy
#: entirely; a client that ignores the system proxy (Flutter/Dart's own
#: HttpClient, Unity, a client with its own gRPC/Firebase sockets) is
#: invisible to it; and once our certificate is installed, a PINNED app's own
#: requests fail -- a failure this capture caused, never a defect in the app.
CAPTURE_LIMITS_NOTE = (
    "Coverage limits: traffic that uses QUIC/HTTP3 over UDP:443 bypasses this "
    "proxy entirely; an app that ignores the system proxy setting (Flutter/Dart's "
    "own HttpClient, Unity, or a client with its own gRPC/Firebase sockets) is "
    "invisible to it; and once our certificate is installed, a PINNED app's own "
    "requests FAIL -- a failure capture caused, never a defect in the app."
)

#: A human label per tier, keyed on the SAME vocabulary
#: ``tools.mobile_capture.ladder`` produces -- never a second spelling
#: drifting from the first.
CAPTURE_TIER_LABELS = {
    capture_tiers.TIER_NONE: "not attempted",
    capture_tiers.TIER_PROXIED: "proxied, not decrypted",
    capture_tiers.TIER_DECRYPTED: "decrypted",
    capture_tiers.TIER_TLS_FAILURE: "TLS failed",
    capture_tiers.TIER_INCONCLUSIVE: "inconclusive",
}


def capture_banner() -> str:
    return '<p class="capban">' + ev_esc(CAPTURE_LIMITS_NOTE, 600) + "</p>"


def capture_body_block(flow: dict) -> str:
    """One flow's bodies, COLLAPSED behind a click (decision 11) under a banner.

    Bodies are already FULL and redacted at WRITE time (Phase 5,
    ``tools.mobile_capture.flows``); this collapses them, it does not redact.
    """
    return (
        '<details class="capbody"><summary>request &amp; response bodies '
        "(redacted, click to open)</summary>"
        '<div class="capbodyinner"><p class="mt">request body</p><pre>'
        + ev_esc(flow.get("request_body"), MAX_REPLY_TEXT)
        + '</pre><p class="mt">response body</p><pre>'
        + ev_esc(flow.get("response_body"), MAX_REPLY_TEXT)
        + "</pre></div></details>"
    )


def _status_cell(status: object) -> str:
    if isinstance(status, int) and not isinstance(status, bool):
        return ev_esc(status, 8)
    return '<span class="mt">no response</span>'


def capture_flow_row(flow: object) -> str:
    body = flow if isinstance(flow, dict) else {}
    return (
        "<tr><td>"
        + ev_esc(body.get("method"), 12)
        + '</td><td dir="auto">'
        + ev_esc(body.get("url"), 300)
        + "</td><td>"
        + _status_cell(body.get("status"))
        + "</td><td>"
        + capture_body_block(body)
        + "</td></tr>"
    )


def _flow_count(case: object) -> int:
    if not isinstance(case, dict):
        return 0
    record = (case.get("evidence") or {}).get("capture")
    if not isinstance(record, dict):
        return 0
    try:
        return max(0, int(record.get("flow_count") or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def capture_flow_total(cases: Any) -> int:
    """Calls observed across every case's capture record."""
    return sum(_flow_count(case) for case in cases or ())
