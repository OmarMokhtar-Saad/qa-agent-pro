"""Folding samples: the port-to-uid map and the app's endpoint map.

Pure and never raising. The caps arrive as a ``limit`` argument: ``netattr``
owns them (tests patch them there) and reads them at call time.
"""

from __future__ import annotations

from typing import Any, NamedTuple, cast


class EndpointSample(NamedTuple):
    """One sample for the endpoint map: its rows, the app uid, the time in ms."""

    rows: Any
    uid: object
    now_ms: Any


# Fold one sample into ``{local_port: [uid, ...]}``.
#
# A port that two samples saw under two uids keeps BOTH: the socket was
# reused, and dropping either one would let the later sample erase the fact
# that the app held that port earlier in the same case.
def merge_capped(samples: object, rows: Any, limit: int) -> dict:
    """Fold one sample into ``{local_port: [uid, ...]}``, at most *limit* ports."""
    merged = dict(samples) if isinstance(samples, dict) else {}
    for row in rows or ():
        _fold(merged, row, limit)
    return merged


def _fold(merged: dict, row: Any, limit: int) -> None:
    """Add one row's uid under its local port, keeping every uid a port had."""
    if not isinstance(row, dict):
        return
    port = row.get("local_port")
    uid = row.get("uid")
    if not isinstance(port, int) or not isinstance(uid, int):
        return
    held = merged.get(port)
    if held is None:
        if len(merged) < limit:
            merged[port] = [uid]
    elif uid not in held:
        held.append(uid)


def _connected(row: dict) -> bool:
    """Did this socket have a peer? A listener and an unbound UDP socket did not.

    Both print an all-zero remote address and port zero, so ONE test covers
    both rather than a state whitelist per protocol -- the state codes differ
    between the TCP and UDP tables and a whitelist would need two rules for one
    question.
    """
    ip = str(row.get("remote_ip") or "")
    port = row.get("remote_port")
    if not ip or not isinstance(port, int) or port <= 0:
        return False
    return bool(ip.strip("0.:"))


def _stamp(now_ms: Any) -> int | None:
    try:
        return int(now_ms)
    except (TypeError, ValueError, OverflowError):
        return None


def _mine(row: Any, uid: int) -> bool:
    """A connected socket row that belongs to *uid*."""
    return isinstance(row, dict) and row.get("uid") == uid and _connected(row)


def _key(row: dict) -> tuple:
    return (
        str(row.get("remote_ip")),
        int(cast("Any", row.get("remote_port"))),
        str(row.get("proto") or "tcp"),
    )


def _new_entry(stamp: int) -> dict:
    return {"first_ms": stamp, "last_ms": stamp, "local_ports": [], "samples": 0}


def _touch(held: dict, row: dict, stamp: int) -> None:
    held["last_ms"] = stamp
    held["samples"] += 1
    port = row.get("local_port")
    if isinstance(port, int) and port not in held["local_ports"]:
        held["local_ports"].append(port)


# Fold one sample's rows into the app's endpoint map.
#
# ``{(ip, port, proto): {"first_ms", "last_ms", "local_ports", "samples"}}``,
# and ONLY for the sample's uid: this map is evidence about the app under test,
# so a socket belonging to anything else is not recorded here at all rather than
# recorded and labelled later. Never raises.
def endpoints_capped(store: object, sample: EndpointSample, limit: int) -> dict:
    """Fold one sample's rows into the app's endpoint map, at most *limit* keys."""
    kept = dict(store) if isinstance(store, dict) else {}
    stamp = _stamp(sample.now_ms)
    if not isinstance(sample.uid, int) or stamp is None:
        return kept
    for row in sample.rows or ():
        if not _mine(row, sample.uid):
            continue
        held = kept.get(_key(row))
        if held is None:
            if len(kept) >= limit:
                continue
            held = kept[_key(row)] = _new_entry(stamp)
        _touch(held, row, stamp)
    return kept
