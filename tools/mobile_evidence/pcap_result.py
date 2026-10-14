"""The parse envelope: notes, counters and rows of one capture."""

from __future__ import annotations

from typing import Any, cast

from tools.mobile_evidence.pcap_read import (
    Limits,
    _Header,
    _header,
    _read_records,
    _Scan,
)
from tools.mobile_evidence.pcap_rows import _rows


def _parse_notes(head: _Header, truncated: bool, scan: _Scan) -> list:
    """The caveats a parse carries, one string each."""
    notes = []
    if not head.supported:
        notes.append(
            "the capture link type is " + str(head.linktype) + ", which this parser "
            "does not read, so no flow was extracted from it"
        )
    if truncated:
        notes.append("the capture was longer than this parser reads, so it is partial")
    if scan.dropped:
        notes.append(
            str(scan.dropped)
            + " flow(s) past the "
            + str(scan.limits.max_flows)
            + "-flow limit were not counted"
        )
    return notes


def _parse_result(scan: _Scan, truncated: bool, head: _Header) -> dict:
    """The successful parse envelope for the flows read."""
    notes = _parse_notes(head, truncated, scan)
    return {
        "error": None,
        "content": {
            "rows": _rows(scan.flows),
            "packets": scan.packets,
            "bytes": scan.total,
            "flows": len(scan.flows),
            "dropped_flows": scan.dropped,
            "unparsed": scan.unparsed,
            "truncated": truncated,
            "linktype": int(head.linktype),
            "note": "; ".join(notes),
        },
    }


def parse_with(data: object, limits: Limits) -> dict:
    """:func:`tools.mobile_evidence.pcap.parse` under explicit ``limits``."""
    try:
        raw = bytes(cast("Any", data) or b"")
    except (TypeError, ValueError):
        return {"error": "the capture was not readable as bytes", "content": None}
    truncated = len(raw) > limits.max_bytes
    if truncated:
        raw = raw[: limits.max_bytes]
    head, error = _header(raw)
    if head is None:
        return {"error": error, "content": None}
    scan = _read_records(raw, head, limits)
    return _parse_result(scan, truncated or scan.cut, head)
