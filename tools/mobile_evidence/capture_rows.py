"""The parsed network rows and the summary file: scrub, attribute, record, write.

Host names are not secrets and are left alone. A plaintext request TARGET is a
different thing: it carries the path and the query string, and no scrubber can see
a national id in a path, so a row never keeps one.
"""

from __future__ import annotations

from typing import Any, NamedTuple, cast

from tools.mobile import run_store
from tools.mobile_evidence import capture_consts as _consts
from tools.mobile_evidence import netattr
from tools.mobile_evidence.capture_records import _network_record


class _ParsedParts(NamedTuple):
    prior: dict
    body: dict
    state: dict
    rows: list
    notes: list[str]


class _SummaryDest(NamedTuple):
    run_id: str
    tc_id: str
    case_index: object


def _scrub_rows(rows: Any) -> list:
    """The rows with every request line dropped before anything reaches disk.

    Host names are not secrets and are left alone. A plaintext request TARGET
    is a different thing: it carries the path and the query string, and no
    scrubber can see a national id in a path, so a row never keeps one.
    ``targets`` stays an empty list so the record's shape does not change.
    """
    out = []
    for row in rows or ():
        if not isinstance(row, dict):
            continue
        record = dict(row)
        record["targets"] = []
        out.append(record)
    return out


def _offset_rows(rows: list, started_ms: object) -> list:
    for row in rows:
        first = row.get("first_ms")
        row["first_offset_ms"] = (
            int(first - started_ms)
            if isinstance(first, int) and isinstance(started_ms, int)
            else None
        )
    return rows


def _attributed_rows(body: dict, state: dict, prior: dict) -> tuple[list, list]:
    """The scrubbed, capped rows with their offsets, and the socket-table rows merged in."""
    # THE UNION, and the wire comes first because only it can carry a name.
    # The socket table contributes every endpoint the capture did not
    # describe -- which on a real device, or on an emulator whose traffic
    # does not cross the tapped interface, is all of them.
    sampled = netattr.endpoint_rows(state.get("endpoints"))
    rows = _scrub_rows(
        netattr.merge_rows(
            netattr.attribute(
                body.get("rows") or [], state.get("samples") or {}, prior.get("uid")
            ),
            sampled,
        )
    )[: _consts.MAX_NETWORK_ROWS]
    return _offset_rows(rows, prior.get("started_ms")), sampled


def _parsed_record(parts: _ParsedParts) -> dict:
    prior, body, state = parts.prior, parts.body, parts.state
    return _network_record(
        stage="parsed",
        started=True,
        file=str(prior.get("file") or ""),
        uid=prior.get("uid"),
        rows=parts.rows,
        packets=int(body.get("packets") or 0),
        samples=int(state.get("taken") or 0),
        truncated=bool(body.get("truncated")),
        note="; ".join(note for note in parts.notes if note),
        started_ms=prior.get("started_ms"),
    )


def _write_network_summary(dest: _SummaryDest, record: dict) -> None:
    """Write the summary file and set ``record["path"]`` to where it landed."""
    written = run_store.write_evidence_json(
        run_store.EvidenceRef(
            dest.run_id,
            dest.tc_id,
            "network-" + str(max(0, int(cast("Any", dest.case_index) or 0))) + ".json",
        ),
        {
            "rows": record["rows"],
            "packets": record["packets"],
            "samples": record["samples"],
            "truncated": record["truncated"],
            "note": record["note"],
        },
    )
    record["path"] = str((written.get("content") or {}).get("path") or "")
