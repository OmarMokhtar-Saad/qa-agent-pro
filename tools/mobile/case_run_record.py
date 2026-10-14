"""Normalised evidence, network and capture records of one case."""

from __future__ import annotations

from typing import Any


def _merge_crash(prior: object, fresh: object) -> dict:
    """The case's crash record across submits: FIRST crash wins.

    A case is submitted many times -- three escapes plus eight budget stops --
    and the app died on whichever replay it died on. The terminal submit's own
    slice may be perfectly clean, so a last-write-wins record would forget the
    death that already happened. Never raises.
    """
    for candidate in (prior, fresh):
        if isinstance(candidate, dict) and candidate.get("detected"):
            return candidate
    return {}


def _holder_body(source: object) -> tuple[dict, dict]:
    """``(holder, body)``: the reply as given and the record inside it."""
    holder = source if isinstance(source, dict) else {}
    body = holder.get("content") if "content" in holder else holder
    return holder, body if isinstance(body, dict) else {}


def _count(value: Any) -> int:
    """A stored count, coerced; a corrupt one is a zero."""
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _evidence_head(holder: dict, body: dict) -> dict:
    """The evidence record's scalar keys and the slice counter, as an int."""
    written = body.get("slices_written")
    return {
        "profile": body.get("profile"),
        "clock_offset_ms": body.get("clock_offset_ms"),
        "pid": body.get("pid"),
        "skipped": body.get("skipped") or holder.get("error") or None,
        "slices": _count(body.get("slices")),
        "slices_written": list(written) if isinstance(written, list) else [],
    }


# crash: present whatever is on disk, so the report never reads a missing key
# and a record written before this feature reads as "no crash" rather than
# raising inside a checkpoint.
# network: carried forward the same way the crash is, and normalised by its own
# accessor: a checkpoint written before this feature has no such key, and the
# report must read a shape rather than a KeyError.
# capture: carried forward the same way "network" is: a checkpoint written
# before this feature has no such key, and the report must read a shape rather
# than a KeyError.
def _evidence_tail(body: dict) -> dict:
    """The evidence record's crash, network and capture keys."""
    crash = body.get("crash")
    return {
        "crash": crash if isinstance(crash, dict) and crash.get("detected") else {},
        "network": network_record(body.get("network")),
        "capture": capture_record(body.get("capture")),
    }


def _evidence_record(source: object) -> dict:
    """The case's evidence record, normalised from ``capture.begin_case``'s
    reply OR from a record already on disk. Every key present, so the report
    never reads a missing one, and the slice counter is an int."""
    holder, body = _holder_body(source)
    return {**_evidence_head(holder, body), **_evidence_tail(body)}


def _network_head(holder: dict, body: dict) -> dict:
    """The network record's keys from ``skipped`` to ``rows``."""
    uid = body.get("uid")
    rows = body.get("rows")
    return {
        "skipped": body.get("skipped") or holder.get("error") or None,
        "stage": str(body.get("stage") or ""),
        "started": bool(body.get("started")),
        "file": str(body.get("file") or ""),
        "uid": uid if isinstance(uid, int) else None,
        "rows": [row for row in (rows or ()) if isinstance(row, dict)],
    }


def _network_tail(body: dict) -> dict:
    """The network record's keys from ``packets`` to ``started_ms``."""
    started_ms = body.get("started_ms")
    return {
        "packets": _count(body.get("packets")),
        "samples": _count(body.get("samples")),
        "truncated": bool(body.get("truncated")),
        "note": str(body.get("note") or ""),
        "path": str(body.get("path") or ""),
        "started_ms": started_ms if isinstance(started_ms, int) else None,
    }


def network_record(source: object) -> dict:
    """The case network record, normalised from ``capture.begin_network`` or
    ``finish_network``, OR from a record already on disk.

    EVERY KEY PRESENT, including on a checkpoint written before this feature
    existed, where each one is its empty value. The report reads these keys; a
    missing one is a KeyError inside a page, which is not a gap the page can
    state. Never raises: a corrupt count on disk is a zero here, not an
    exception inside a checkpoint."""
    holder, body = _holder_body(source)
    return {**_network_head(holder, body), **_network_tail(body)}


def capture_record(source: object) -> dict:
    """The case's API-capture flow record, normalised from
    ``api_flows.mark_case_current`` / ``finish_case``, OR from a record
    already on disk. EVERY KEY PRESENT; never raises. Beside
    ``network_record`` -- a second, independent dimension on the same
    per-case evidence seam: this lane sees inside TLS, the pcap lane does
    not."""
    holder, body = _holder_body(source)
    flows = body.get("flows")
    return {
        "skipped": body.get("skipped") or holder.get("error") or None,
        "flow_count": _count(body.get("flow_count")),
        "flows": [row for row in (flows or ()) if isinstance(row, dict)],
        "truncated_by_count": bool(body.get("truncated_by_count")),
    }
