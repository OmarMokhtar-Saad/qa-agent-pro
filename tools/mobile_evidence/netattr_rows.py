"""Report rows: the endpoint map as rows, the join with the wire, the owner label.

Pure and never raising.
"""

from __future__ import annotations

from typing import Any

from tools.mobile_evidence.netattr_labels import (
    OWNER_APP,
    OWNER_OTHER,
    OWNER_UNSAMPLED,
    SOURCE_SOCKET,
)


def _endpoint_row(key: tuple, held: dict) -> dict:
    ip, port, proto = key
    return {
        "host": ip,
        "server": ip,
        "port": port,
        "proto": proto,
        "source": SOURCE_SOCKET,
        "owner": OWNER_APP,
        "connections": max(1, len(held.get("local_ports") or ())),
        "local_ports": list(held.get("local_ports") or ()),
        "packets": None,
        "bytes": None,
        "first_ms": held.get("first_ms"),
        "last_ms": held.get("last_ms"),
        "undetermined": False,
    }


# The app's endpoint map as report rows, in the shape the parser emits.
#
# ``bytes`` and ``packets`` are ``None`` and not zero: this source counts
# sockets, it does not weigh traffic, and a nought in those columns would read
# as "it sent nothing". ``owner`` is the app BY CONSTRUCTION -- the map is
# built from the app's own uid -- so it is set here rather than inferred later
# from a port that a second sample might have seen under somebody else.
def endpoint_rows(store: Any) -> list:
    """The app's endpoint map as report rows, in the shape the parser emits."""
    rows = []
    for key, held in (store or {}).items():
        if isinstance(held, dict):
            rows.append(_endpoint_row(key, held))
    return sorted(rows, key=lambda row: (str(row["host"]), int(row["port"])))


def _identity(row: dict) -> tuple:
    return (str(row.get("server") or ""), row.get("port"))


def _seen(out: list) -> set:
    return {_identity(row) for row in out if row.get("server")}


def _novel(sampled: Any, seen: set) -> list:
    return [
        row
        for row in sampled or ()
        if isinstance(row, dict) and _identity(row) not in seen
    ]


# Wire rows first, then every endpoint the wire did not already describe.
#
# The join is on the SERVER ADDRESS and port, which is the one identity both
# sources can state: a wire row may carry a name the socket table cannot know,
# and the socket table may carry an endpoint the capture never saw. A wire row
# with no server (nothing identified a client) matches nothing and is kept as
# it is.
def merge_rows(named: Any, sampled: Any) -> list:
    """Wire rows first, then every endpoint the wire did not already describe."""
    out = [row for row in (named or ()) if isinstance(row, dict)]
    return out + _novel(sampled, _seen(out))


def owner_of(local_ports: Any, samples: object, uid: object) -> str:
    """The owner label for one row. One rule, read by one caller."""
    ports = [port for port in (local_ports or ()) if isinstance(port, int)]
    if not ports:
        return OWNER_UNSAMPLED
    table = samples if isinstance(samples, dict) else {}
    seen: list[Any] = []
    for port in ports:
        seen.extend(table.get(port) or ())
    if not seen:
        return OWNER_UNSAMPLED
    if isinstance(uid, int) and uid in seen:
        return OWNER_APP
    if not isinstance(uid, int):
        # The uid is unknown, so "another app" is not something this sample can
        # establish either -- only that SOMEBODY owned it, which is not a fact
        # about the app under test.
        return OWNER_UNSAMPLED
    return OWNER_OTHER


def attribute(rows: Any, samples: object, uid: object) -> list:
    """Every row with an ``owner``. The rows are copied, never mutated in place."""
    out = []
    for row in rows or ():
        if not isinstance(row, dict):
            continue
        record = dict(row)
        record["owner"] = owner_of(record.get("local_ports"), samples, uid)
        out.append(record)
    return out
