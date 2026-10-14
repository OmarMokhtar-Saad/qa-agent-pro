"""The per-case and run-level API-capture sections."""

from __future__ import annotations

from typing import Any

from tools.mobile_evidence.render_block import SecHead, sec_block
from tools.mobile_evidence.render_capture import (
    CAPTURE_TIER_LABELS,
    capture_banner,
    capture_flow_row,
    capture_flow_total,
)
from tools.mobile_evidence.render_esc import MAX_ROWS, ev_esc
from tools.mobile_evidence.render_html import empty
from tools.mobile_shared import capture_tiers

_HEAD = (
    '<div class="tablewrap"><table class="cov"><thead><tr>'
    '<th scope="col">Verb</th><th scope="col">URL</th>'
    '<th scope="col">Status</th><th scope="col">Body</th>'
    "</tr></thead><tbody>"
)


def _case_flows(record: object) -> list | None:
    holder = record if isinstance(record, dict) else {}
    flows = holder.get("flows")
    if not isinstance(flows, list):
        return None
    return [f for f in flows if isinstance(f, dict)]


def _flows_table(flows: list) -> str:
    rows = "".join(capture_flow_row(flow) for flow in flows[:MAX_ROWS])
    return capture_banner() + _HEAD + rows + "</tbody></table></div>"


def _flows_block(flows: list) -> str:
    head = SecHead(
        total=len(flows),
        note="calls we observed while this case replayed, bodies redacted and "
        "collapsed",
    )
    return sec_block("APIs we observed", _flows_table(flows), head)


def _empty_block(why: str, note: str) -> str:
    return sec_block("APIs we observed", empty(why), SecHead(note=note))


def case_capture(record: object) -> str:
    """The per-case "APIs we observed" block (T7.1): always a section, with WHY.

    Wording is always "calls we OBSERVED": a proxy sees what crossed IT.
    """
    flows = _case_flows(record)
    if flows is None:
        return _empty_block(
            "no API capture was taken for this case", "no capture was taken"
        )
    if not flows:
        return _empty_block(
            "capture ran and observed no calls we could name while this case replayed",
            "captured, and empty",
        )
    return _flows_block(flows)


def _tier_line(tier: Any, flow_total: int) -> str:
    return (
        '<p class="captier">Capture reached tier: <b>'
        + ev_esc(CAPTURE_TIER_LABELS.get(tier, tier), 40)
        + "</b>. Calls we observed across this run: "
        + str(flow_total)
        + ".</p>"
    )


def _reason_line(reason: Any, message: Any) -> str:
    return (
        '<p class="capreason">Not decrypted -- reason: <b>'
        + ev_esc(reason or "unknown", 40)
        + "</b>. "
        + ev_esc(message or "No further detail was recorded.", 300)
        + "</p>"
    )


_PIN_LINE = (
    '<p class="cappin">A request failure shown below on this run may '
    "be capture's own doing, not the app's: the app pins its own "
    "certificates, so its calls fail once our certificate is "
    "installed. That is not an app defect.</p>"
)


_SECTION_HEAD = SecHead(
    note="calls we observed on this run, and the coverage this capture cannot reach"
)


def capture_section(cases: object, manifest: object) -> str:
    """The run-level API-capture section (T7.2): tier, reason, limits banner.

    A failure THIS capture caused (a pinned app) is attributed to capture,
    never left to read as an app defect.
    """
    body = manifest if isinstance(manifest, dict) else {}
    capture: Any = body.get("capture") if isinstance(body.get("capture"), dict) else {}
    tier = capture.get("tier")
    tier = tier if tier in capture_tiers.TIERS else capture_tiers.TIER_NONE
    reason = capture.get("reason")
    lines = [_tier_line(tier, capture_flow_total(cases))]
    if tier != capture_tiers.TIER_DECRYPTED:
        lines.append(_reason_line(reason, capture.get("message")))
    if reason == capture_tiers.REASON_PINNED:
        lines.append(_PIN_LINE)
    lines.append(capture_banner())
    return sec_block("API capture", "".join(lines), _SECTION_HEAD)
