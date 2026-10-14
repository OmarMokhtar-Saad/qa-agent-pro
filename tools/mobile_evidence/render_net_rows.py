"""Host rows, the table and the notes of the network capture."""

from __future__ import annotations

from tools.mobile_evidence import netattr, pcap
from tools.mobile_evidence.render_esc import MAX_ROWS, ev_esc
from tools.mobile_evidence.render_net import net_cell, net_offset

# NO REQUEST LINE IS DRAWN. The row's `targets` (method and request-target of a
# plaintext HTTP call) are gone with their producer: a path can carry a patient
# id or a record id and no key-based scrubber can see it, so it never reaches
# the page. Do not repopulate that key here.

_HEAD = (
    '<div class="tablewrap"><table class="cov"><thead><tr>'
    '<th scope="col">Host</th><th scope="col">Port</th>'
    '<th scope="col">Protocol</th><th scope="col">Connections</th>'
    '<th scope="col">Bytes</th><th scope="col">First seen</th>'
    '<th scope="col">How it was named</th><th scope="col">Owner</th>'
)


def _name_cells(row: dict) -> str:
    port = row.get("port")
    return (
        '<tr><td class="uc"><code>'
        + ev_esc(row.get("host"), pcap.MAX_NAME_CHARS)
        + "</code>"
        + "</td><td>"
        + (ev_esc(str(port), 8) if isinstance(port, int) else "-")
        + '</td><td><span class="cap">'
        + ev_esc(row.get("proto"), 8)
    )


def _measure_cells(row: dict) -> str:
    return (
        "</span></td><td>"
        + net_cell(row.get("connections"))
        + "</td><td>"
        + net_cell(row.get("bytes"))
        + "</td><td>"
        + net_offset(row)
    )


def net_row(row: dict, extra: str = "") -> str:
    return (
        _name_cells(row)
        + _measure_cells(row)
        + "</td><td>"
        + ev_esc(row.get("source"), 20)
        + '</td><td><span class="netown">'
        + ev_esc(row.get("owner") or netattr.OWNER_UNSAMPLED, 40)
        + "</span></td>"
        + extra
        + "</tr>"
    )


def net_table(rows: list, head_extra: str = "", row_extra=None) -> str:
    body = "".join(
        net_row(row, row_extra(row) if row_extra else "") for row in rows[:MAX_ROWS]
    )
    return _HEAD + head_extra + "</tr></thead><tbody>" + body + "</tbody></table></div>"


def _unsampled_note() -> str:
    return (
        "a row whose owner says "
        + netattr.OWNER_UNSAMPLED
        + " is traffic this run saw on the wire but never caught in a socket "
        + "sample, so it is neither claimed for the app nor hidden"
    )


def net_notes(record: dict, rows: list) -> str:
    notes = []
    if any(str(row.get("source")) == pcap.SOURCE_SNI for row in rows):
        notes.append(pcap.TLS_HIDES_THE_PATH)
    if record["truncated"]:
        notes.append(
            "the capture was longer than this server reads, so these rows are partial"
        )
    if record["note"]:
        notes.append(record["note"])
    if any(str(row.get("owner")) == netattr.OWNER_UNSAMPLED for row in rows):
        notes.append(_unsampled_note())
    return (
        '<p class="netcap">' + ev_esc("; ".join(notes), 1200) + "</p>" if notes else ""
    )
