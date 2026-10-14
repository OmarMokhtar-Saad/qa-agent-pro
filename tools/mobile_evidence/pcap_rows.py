"""Flows as report rows: one row per host a case reached."""

from __future__ import annotations

from typing import NamedTuple

from tools.mobile_evidence.pcap_const import (
    PROTO_DNS,
    PROTO_HTTP,
    PROTO_TLS,
    SOURCE_DNS,
    SOURCE_HTTP,
    SOURCE_IP,
    SOURCE_SNI,
)


def _merge(rows: dict, row: dict) -> None:
    key = (row["host"], row["port"], row["proto"], row["source"])
    held = rows.get(key)
    if held is None:
        rows[key] = row
        return
    held["connections"] += row["connections"]
    held["packets"] += row["packets"]
    _add_bytes(held, row)
    _span_ms(held, row)
    _add_ports(held, row)


def _add_bytes(held: dict, row: dict) -> None:
    """Add the bytes of ``row`` to ``held``; one unmeasured side poisons the sum."""
    if held["bytes"] is None or row["bytes"] is None:
        held["bytes"] = None
    else:
        held["bytes"] += row["bytes"]


def _span_ms(held: dict, row: dict) -> None:
    """Widen the time span of ``held`` to cover ``row``."""
    for name in ("first_ms", "last_ms"):
        mine, theirs = held[name], row[name]
        if theirs is None:
            continue
        if mine is None:
            held[name] = theirs
        else:
            held[name] = min(mine, theirs) if name == "first_ms" else max(mine, theirs)


def _add_ports(held: dict, row: dict) -> None:
    """Add the local ports of ``row`` that ``held`` does not list."""
    for port in row["local_ports"]:
        if port not in held["local_ports"]:
            held["local_ports"].append(port)


def _flow_base(conv: dict) -> dict:
    """The fields every row of one flow shares."""
    client, server = conv["client"], conv["server"]
    return {
        "packets": conv["packets"],
        "bytes": conv["bytes"],
        "first_ms": conv["first_ms"],
        "last_ms": conv["last_ms"],
        "local_ports": [client[1]] if client else [],
        "connections": 1,
        # The ADDRESS, beside whatever name this flow managed to learn. The
        # socket table can state an address and never a name, so this is the
        # only field the two sources can be joined on.
        "server": server[0] if server else None,
        "undetermined": False,
    }


def _dns_rows(conv: dict, base: dict) -> list:
    """One row per name a DNS flow asked for."""
    server = conv["server"]
    port = server[1] if server else None
    return [_dns_row(base, item, port) for item in conv["qnames"].items()]


# NOT a share of the flow bytes: a per-name byte count
# is not a thing this capture measured, and a number
# nobody measured is worse on a page than a blank.
def _dns_row(base: dict, item: tuple, port: object) -> dict:
    """One DNS row for ``item`` (name, count), keyed like the flow base."""
    name, count = item
    return {
        **base,
        "host": name,
        "port": port,
        "proto": PROTO_DNS,
        "source": SOURCE_DNS,
        "connections": count,
        "bytes": None,
    }


def _flow_row(conv: dict, base: dict) -> dict:
    """The single row of a non-DNS flow, named by the best evidence it has."""
    return {**base, **_flow_naming(conv)}


# KEYED ON THE HOST, not on a list of request lines. It was `elif
# conv["targets"]`, and dropping the request target would have made
# this branch unreachable -- every plaintext row would have fallen
# through to ip-only and the feature would have silently stopped
# naming hosts it had correctly parsed.
def _flow_naming(conv: dict) -> dict:
    """The row fields naming a non-DNS flow's far end, by its best evidence."""
    server = conv["server"]
    if conv["sni"]:
        return _naming(conv["sni"], server[1], Via(PROTO_TLS, SOURCE_SNI))
    if conv["http_host"]:
        return _naming(conv["http_host"], server[1], Via(PROTO_HTTP, SOURCE_HTTP))
    if server is not None:
        return _naming(server[0], server[1], Via(conv["transport"], SOURCE_IP))
    return _undetermined(conv)


def _undetermined(conv: dict) -> dict:
    """The row of a flow in which nothing identified a client."""
    # NOTHING in this flow identified a client, so neither end is the
    # server. Said, not guessed -- and with no local port, so the owner
    # attribution can only report it unsampled.
    naming = _naming(conv["ends"], None, Via(conv["transport"], SOURCE_IP))
    naming.update({"local_ports": [], "undetermined": True})
    return naming


class Via(NamedTuple):
    """How a flow was named: its protocol label and its source label."""

    proto: str
    source: str


def _naming(host: object, port: object, via: Via) -> dict:
    """The row fields that name a flow's far end."""
    return {"host": host, "port": port, "proto": via.proto, "source": via.source}


def _rows(flows: dict) -> list:
    merged: dict = {}
    for conv in flows.values():
        base = _flow_base(conv)
        if conv["qnames"]:
            for row in _dns_rows(conv, base):
                _merge(merged, row)
        else:
            _merge(merged, _flow_row(conv, base))
    return sorted(
        merged.values(),
        key=lambda item: (-int(item["connections"]), str(item["host"])),
    )
