"""The run-level network roll-up: per-case rows merged, with the cases each was seen in."""

from __future__ import annotations

from typing import Any, cast

from tools.mobile_evidence import netattr
from tools.mobile_evidence.render_block import SecHead, sec_block
from tools.mobile_evidence.render_html import empty
from tools.mobile_evidence.render_net import NO_RECORD, net
from tools.mobile_evidence.render_net_rows import net_notes, net_table

_ERRS = (TypeError, ValueError, OverflowError)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _row_key(row: dict) -> tuple:
    return (
        str(row.get("host")),
        row.get("port"),
        str(row.get("proto")),
        str(row.get("source")),
        str(row.get("owner") or netattr.OWNER_UNSAMPLED),
    )


def _fold_offset(held: dict, row: dict) -> None:
    # THE EARLIEST offset, not the first case's: a host reached in every case
    # is described by the soonest it was ever reached.
    mine = held.get("first_offset_ms")
    theirs = row.get("first_offset_ms")
    if _is_int(theirs):
        held["first_offset_ms"] = (
            theirs if not _is_int(mine) else min(cast(int, mine), cast(int, theirs))
        )


def _fold_counts(held: dict, row: dict) -> None:
    for name in ("connections", "packets"):
        try:
            held[name] = int(held.get(name) or 0) + int(row.get(name) or 0)
        except _ERRS:
            held[name] = held.get(name)


def _fold_bytes(held: dict, row: dict) -> None:
    if held.get("bytes") is None or row.get("bytes") is None:
        held["bytes"] = None
        return
    try:
        held["bytes"] = int(held["bytes"]) + int(row["bytes"])
    except _ERRS:
        held["bytes"] = None


def _merge_row(merged: dict, row: dict, tc_id: str) -> None:
    key = _row_key(row)
    held = merged.get(key)
    if held is None:
        held = dict(row)
        held["cases"] = set()
        merged[key] = held
    else:
        _fold_offset(held, row)
        _fold_counts(held, row)
        _fold_bytes(held, row)
    if tc_id:
        held["cases"].add(tc_id)


def network_rows(cases: Any) -> list:
    """Run-level rows: the per-case rows merged, with the cases each was seen in."""
    merged: dict = {}
    for case in cases or ():
        holder = case if isinstance(case, dict) else {}
        tc_id = str(holder.get("tc_id") or "")
        record = net((holder.get("evidence") or {}).get("network"))
        for row in record["rows"]:
            _merge_row(merged, row, tc_id)
    return sorted(
        merged.values(),
        key=lambda item: (-int(item.get("connections") or 0), str(item.get("host"))),
    )


def _said(records: list) -> str:
    """Why a run with no rows has none."""
    reasons = sorted({r["skipped"] for r in records if r["skipped"]})
    if reasons:
        return reasons[0]
    if not any(r["present"] for r in records):
        # NO case carried a record: "captured nothing" would report a capture
        # that never ran.
        return NO_RECORD
    return "no case in this run captured any network traffic"


def _cases_cell(row: dict) -> str:
    return "<td>" + str(len(row.get("cases") or ())) + "</td>"


def _rolled(records: list) -> dict:
    return {
        "truncated": any(r["truncated"] for r in records),
        "note": "; ".join(sorted({r["note"] for r in records if r["note"]}))[:1200],
    }


def _records(cases: Any) -> list:
    return [
        net((case.get("evidence") or {}).get("network"))
        for case in (cases or ())
        if isinstance(case, dict)
    ]


def network_section(cases: Any) -> str:
    """The run-level roll-up, appended under the API surface section."""
    rows = network_rows(cases)
    records = _records(cases)
    if not rows:
        return sec_block(
            "On the wire",
            empty(_said(records)),
            SecHead(note="the emulator own packet capture"),
        )
    head = '<th scope="col">Cases</th>'
    table = net_table(rows, head_extra=head, row_extra=_cases_cell)
    return sec_block(
        "On the wire",
        net_notes(_rolled(records), rows) + table,
        SecHead(
            total=len(rows),
            note="every host reached inside a case, off the emulator own capture",
        ),
    )
