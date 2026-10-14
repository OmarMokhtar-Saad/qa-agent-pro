"""The result records of the app-evidence capture, one shape per outcome.

Every record carries EVERY key, so no consumer reads a missing one. The network
record is built by :func:`_network_record`; a skipped or refused outcome is a
record with a reason, never a blank and never a raise.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from tools.mobile_evidence import capture_consts as _consts


class _Judged(NamedTuple):
    line_count: int
    crash: dict


class _EventStats(NamedTuple):
    segments: int
    lines: int
    truncated: bool


def _slice_skipped(reason: str) -> dict:
    return {
        "error": None,
        "content": {
            "skipped": reason,
            "path": None,
            "lines": 0,
            "truncated": False,
            "crash": None,
        },
    }


def _slice_record(written: dict, body: dict, judged: _Judged) -> dict:
    return {
        "error": None,
        "content": {
            "skipped": None,
            "path": str((written.get("content") or {}).get("path") or ""),
            "lines": judged.line_count,
            "truncated": bool(body.get("truncated")),
            "crash": judged.crash,
        },
    }


def _logcat_only(reason: str) -> dict:
    return {
        "error": None,
        "content": {
            "events_source": "logcat-only",
            "path": None,
            "reason": str(reason)[:300],
            "segments": 0,
            "lines": 0,
            "truncated": False,
        },
    }


def _events_record(written: dict, stats: _EventStats) -> dict:
    return {
        "error": None,
        "content": {
            "events_source": "ndjson",
            "path": str((written.get("content") or {}).get("path") or ""),
            "reason": ("the " + str(_consts.MAX_EVENTS_BYTES) + " byte cap was reached")
            if stats.truncated
            else None,
            "segments": stats.segments,
            "lines": stats.lines,
            "truncated": stats.truncated,
        },
    }


def _network_record(**over) -> dict:
    """One network record with EVERY key present, so no consumer reads a missing one."""
    body: dict[str, Any] = {
        "skipped": None,
        "stage": "",
        "started": False,
        "file": "",
        "uid": None,
        "rows": [],
        "packets": 0,
        "samples": 0,
        "truncated": False,
        "note": "",
        "path": "",
        "started_ms": None,
    }
    body.update(over)
    return body


def _network_skipped(reason: object, stage: str = "") -> dict:
    return {
        "error": None,
        "content": _network_record(skipped=str(reason)[:300], stage=str(stage)[:40]),
    }


def _not_started(prior: dict) -> dict:
    return _network_skipped(
        prior.get("skipped") or "no network capture was started for this case",
        str(prior.get("stage") or "start"),
    )
