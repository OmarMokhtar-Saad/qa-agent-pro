"""Whose socket was that: the owner column of /proc/net, and what it can prove.

THE PROBLEM. An emulator system image talks constantly -- Play services, remote
config, measurement. A capture of the wire is therefore MOSTLY not the app under
test, and a host list with no owner column would tell a tester the app called
services it has never heard of.

THE ANSWER, and it needs no root. ``/proc/net/tcp`` and ``/proc/net/tcp6`` are
readable from an ordinary ``adb shell`` and carry a uid column. The app uid comes
from ``dumpsys package <pkg>``. A flow local port seen against that uid in a
sample is the app; seen against another uid it is another app; never seen at all
it is UNKNOWN, and that third state is the whole point.

**Sockets are short-lived, so the table is SAMPLED while the case replays** --
not read once at the end, when the sockets the case opened are already gone. A
flow no sample saw is reported :data:`OWNER_UNSAMPLED`. It is never dropped
(that would hide real traffic) and never called the app (that would invent
evidence). Three states, three labels, and the renderer prints a different thing
for each.

**ONE match rule, on the local port alone.** Matching additionally on the remote
address would be a second derivation of the same question in a second place, and
the two would answer differently the first time a socket was reused -- the
mirrored-condition failure. A local port identifies a socket at an instant, and
a sample IS an instant; that is exactly the claim being made.

Pure and never raising: text in, records out.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

#: Lines of one /proc/net table this parser reads. A busy emulator shows a few
#: hundred sockets; the cap is what stops a device that answers with a file
#: instead of a table from becoming this process memory, and it is applied per
#: sample, of which there are at most as many as the capture takes.
MAX_PROC_LINES = 4000

#: Local ports remembered across every sample of one case. 65535 is the whole
#: port space, so this is the arithmetic ceiling rather than a taste -- past it
#: the map cannot be describing ports on one device.
MAX_SAMPLED_PORTS = 65535

#: Distinct endpoints remembered for one case. A case reaches a handful of
#: hosts and the system image adds its own; past this a NEW endpoint is dropped
#: and counted rather than silently merged, the same rule the packet parser
#: uses for flows, and for the same reason: a page nobody can read is not a
#: report.
MAX_TRACKED_ENDPOINTS = 400

#: What a row from the socket table says it is. The parser's own labels live in
#: ``pcap.SOURCES``; this one has a different producer, so it is named here.
SOURCE_SOCKET = "socket-table"

#: What the page says about a socket-table row, so nobody reads an address as a
#: host that could not be named. Stated once, here, because the reason is a
#: property of the SOURCE and not of any one row.
SOCKET_NOTE = (
    "rows named socket-table come from the device's own socket list, which "
    "records the address a connection went to and never the name asked for or "
    "the path requested"
)

OWNER_APP = "this app"
OWNER_OTHER = "other app on device"
OWNER_UNSAMPLED = "owner not sampled"

#: Every owner label this module can emit. The renderer asserts it can draw each
#: one: one producer, one meaning, every consumer.
OWNERS = (OWNER_APP, OWNER_OTHER, OWNER_UNSAMPLED)

#: What the page says when the uid could not be read at all. Then EVERY row is
#: unsampled, and the reason is the missing uid rather than a missing sample.
NO_UID_NOTE = (
    "the package uid could not be read, so no flow in this case could be "
    "attributed to the app rather than to the rest of the device"
)

_UID_RE = re.compile(r"\b(?:userId|uid)=(\d{1,7})\b")


def app_uid(dumpsys_text: object) -> int | None:
    """The app uid from ``dumpsys package <pkg>``, or None.

    Both spellings are accepted because both ship: the package dump prints
    ``userId=`` and some builds print ``uid=``. The FIRST match wins -- the
    dump repeats the number for every user profile, and later lines describe
    other users of the same app.
    """
    match = _UID_RE.search(str(dumpsys_text or ""))
    if not match:
        return None
    try:
        value = int(match.group(1))
    except (TypeError, ValueError, OverflowError):
        return None
    return value if value >= 0 else None


def _hex_port(text: str) -> int | None:
    try:
        value = int(text, 16)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if 0 <= value <= 65535 else None


def _ip_from_hex(text: object) -> str:
    """One ``/proc/net`` address as text, or ``""``.

    The kernel prints the address as 32-bit words in HOST order, so each group
    of eight hex characters is four bytes to be read backwards. A v4-mapped v6
    address is printed as the v4 it is, for the same reason the packet parser
    does it: one host must not become two rows on the page.
    """
    raw = str(text or "").strip()
    if len(raw) not in (8, 32):
        return ""
    try:
        octets: list = []
        for start in range(0, len(raw), 8):
            octets.extend(reversed(bytes.fromhex(raw[start : start + 8])))
    except ValueError:
        return ""
    data = bytes(octets)
    if len(data) == 4:
        return ".".join(str(byte) for byte in data)
    if data[:10] == b"\x00" * 10 and data[10:12] == b"\xff\xff":
        return ".".join(str(byte) for byte in data[12:16])
    return ":".join("%x" % ((data[i] << 8) | data[i + 1]) for i in range(0, 16, 2))


def parse_proc_net(text: object, proto: str = "tcp") -> list:
    """One ``/proc/net`` socket table as rows.

    ``[{"local_port", "uid", "state", "remote_ip", "remote_port", "proto"}]``.
    The remote end is read HERE because this table is the only place a real
    device will say who the app talked to; *proto* is passed in rather than
    guessed from the text, because the four tables are identical in shape and
    only the caller knows which one it read.

    A malformed line is SKIPPED, never raised on and never counted as a socket:
    this text comes off a device, and one unreadable row must not cost the case
    every row after it. The header line is skipped the same way, by failing the
    same checks rather than by a special case -- one rule, not two.
    """
    rows = []
    for index, line in enumerate(str(text or "").splitlines()):
        if index >= MAX_PROC_LINES:
            break
        fields = line.split()
        if len(fields) < 8 or not fields[0].endswith(":"):
            continue
        local = fields[1].split(":")
        if len(local) != 2:
            continue
        port = _hex_port(local[1])
        if port is None:
            continue
        try:
            uid = int(fields[7])
        except (TypeError, ValueError, OverflowError):
            continue
        if uid < 0:
            continue
        remote = str(fields[2]).split(":")
        rows.append(
            {
                "local_port": port,
                "uid": uid,
                "state": str(fields[3])[:4],
                "remote_ip": _ip_from_hex(remote[0]) if len(remote) == 2 else "",
                "remote_port": _hex_port(remote[1]) if len(remote) == 2 else None,
                "proto": str(proto or "tcp")[:8],
            }
        )
    return rows


def merge_samples(samples: object, rows: object) -> dict:
    """Fold one sample into ``{local_port: [uid, ...]}``.

    A port that two samples saw under two uids keeps BOTH: the socket was
    reused, and dropping either one would let the later sample erase the fact
    that the app held that port earlier in the same case.
    """
    merged = dict(samples) if isinstance(samples, dict) else {}
    for row in rows or ():
        if not isinstance(row, dict):
            continue
        port = row.get("local_port")
        uid = row.get("uid")
        if not isinstance(port, int) or not isinstance(uid, int):
            continue
        held = merged.get(port)
        if held is None:
            if len(merged) >= MAX_SAMPLED_PORTS:
                continue
            merged[port] = [uid]
        elif uid not in held:
            held.append(uid)
    return merged


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


def merge_endpoints(store: object, rows: object, uid: object, now_ms: object) -> dict:
    """Fold one sample's rows into the app's endpoint map.

    ``{(ip, port, proto): {"first_ms", "last_ms", "local_ports", "samples"}}``,
    and ONLY for *uid*: this map is evidence about the app under test, so a
    socket belonging to anything else is not recorded here at all rather than
    recorded and labelled later. Never raises.
    """
    kept = dict(store) if isinstance(store, dict) else {}
    if not isinstance(uid, int):
        return kept
    try:
        stamp = int(now_ms)
    except (TypeError, ValueError, OverflowError):
        return kept
    for row in rows or ():
        if not isinstance(row, dict) or row.get("uid") != uid or not _connected(row):
            continue
        key = (
            str(row.get("remote_ip")),
            int(row.get("remote_port")),
            str(row.get("proto") or "tcp"),
        )
        held = kept.get(key)
        if held is None:
            if len(kept) >= MAX_TRACKED_ENDPOINTS:
                continue
            held = {
                "first_ms": stamp,
                "last_ms": stamp,
                "local_ports": [],
                "samples": 0,
            }
            kept[key] = held
        held["last_ms"] = stamp
        held["samples"] += 1
        port = row.get("local_port")
        if isinstance(port, int) and port not in held["local_ports"]:
            held["local_ports"].append(port)
    return kept


def endpoint_rows(store: object) -> list:
    """The app's endpoint map as report rows, in the shape the parser emits.

    ``bytes`` and ``packets`` are ``None`` and not zero: this source counts
    sockets, it does not weigh traffic, and a nought in those columns would read
    as "it sent nothing". ``owner`` is the app BY CONSTRUCTION -- the map is
    built from the app's own uid -- so it is set here rather than inferred later
    from a port that a second sample might have seen under somebody else.
    """
    rows = []
    for key, held in (store or {}).items():
        if not isinstance(held, dict):
            continue
        ip, port, proto = key
        rows.append(
            {
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
        )
    return sorted(rows, key=lambda row: (str(row["host"]), int(row["port"])))


def merge_rows(named: object, sampled: object) -> list:
    """Wire rows first, then every endpoint the wire did not already describe.

    The join is on the SERVER ADDRESS and port, which is the one identity both
    sources can state: a wire row may carry a name the socket table cannot know,
    and the socket table may carry an endpoint the capture never saw. A wire row
    with no server (nothing identified a client) matches nothing and is kept as
    it is.
    """
    out = [row for row in (named or ()) if isinstance(row, dict)]
    seen = {
        (str(row.get("server") or ""), row.get("port"))
        for row in out
        if row.get("server")
    }
    for row in sampled or ():
        if not isinstance(row, dict):
            continue
        if (str(row.get("server") or ""), row.get("port")) in seen:
            continue
        out.append(row)
    return out


def owner_of(local_ports: object, samples: object, uid: object) -> str:
    """The owner label for one row. One rule, read by one caller."""
    ports = [port for port in (local_ports or ()) if isinstance(port, int)]
    if not ports:
        return OWNER_UNSAMPLED
    table = samples if isinstance(samples, dict) else {}
    seen = []
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


def attribute(rows: object, samples: object, uid: object) -> list:
    """Every row with an ``owner``. The rows are copied, never mutated in place."""
    out = []
    for row in rows or ():
        if not isinstance(row, dict):
            continue
        record = dict(row)
        record["owner"] = owner_of(record.get("local_ports"), samples, uid)
        out.append(record)
    return out
