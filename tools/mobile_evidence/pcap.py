"""A libpcap file, read as flows: what the device talked to while a case ran.

PURE. Bytes in, records out. No socket, no adb, no device, no clock -- so every
claim this module makes is checkable against a fixture built in a test, and the
lifecycle around it (``tools/mobile_evidence/capture.py``) can fail in every way
it likes without changing what a packet means.

WHAT IS AND IS NOT VISIBLE, and the report has to say so. The traffic measured
from a real build on 2026-09-09 was TLS on 443 with nothing in plaintext. So a
host name is derivable -- from the TLS ClientHello server-name extension, from
DNS queries, and from a plaintext ``Host:`` header -- and a URL PATH IS NOT
KEPT, on any path, ever.

That last part is a DELIBERATE REDUCTION rather than a limitation. A plaintext
request line was quoted into the report, capped and passed through a query-string
scrubber, and a review found the obvious hole: a scrubber keyed on ``name=value``
cannot see a PATH, so a request target of the shape
``/api/v2/patients/<a national id>/records/<a treatment name>`` travelled into
an HTML file the tester emails onward, carrying both of those in it. There is no allowlist that makes an arbitrary path safe, so
the request target is dropped at the parser rather than masked downstream -- a
value that never enters cannot leak from a consumer somebody adds later.

**WHICH END IS THE CLIENT IS DERIVED, NEVER GUESSED.** A flow is keyed on the
unordered pair of endpoints. The client side is fixed only by a packet that
POSITIVELY identifies itself as client-to-server: a TCP SYN without ACK, a TLS
ClientHello, a well-formed DNS query, or an HTTP request line. A flow in which
none of those was ever seen is reported with :data:`SOURCE_IP` and NO local
port, so the owner attribution downstream reports it as unsampled instead of
attributing somebody else traffic to the app under test. "No evidence of the
wrong kind" is not identification; this is why the undetermined row exists
rather than a fallback rule about port numbers.

**Bounded everywhere**, because the input is a file another process wrote:
:data:`MAX_PCAP_BYTES` of input, :data:`MAX_PACKETS` records, :data:`MAX_FLOWS`
distinct flows, and :data:`MAX_NAME_CHARS` per host name. Every one of them is reported when
it binds -- a truncated read that looked complete is the failure this module
would otherwise create.

**Nothing here raises.** ``{"error", "content"}``, as everywhere else in this
package.
"""

from __future__ import annotations

import logging
import string
import struct

logger = logging.getLogger(__name__)

#: Bytes of one capture file this parser will read. A 10-second probe of an
#: idle emulator produced 507 KB; a whole case is minutes, so this admits a
#: capture roughly fifty times the busiest observed one. It is the bound on
#: what one case can pull into this process, and the file is deleted after.
MAX_PCAP_BYTES = 24 * 1024 * 1024

#: Packet records read from one capture. The measured probes were 709 and 274
#: packets; the cap is what stops a file whose record lengths lie from being an
#: unbounded loop, and a capture that reaches it is reported truncated.
MAX_PACKETS = 200000

#: Distinct flows kept. A case reaches a handful of hosts and the system image
#: adds its own; past this the page is not a report any tester reads, so new
#: flows are DROPPED and counted rather than silently merged.
MAX_FLOWS = 400

#: Characters of one host name. 253 is the longest legal DNS name, so this is
#: the protocol bound rather than a taste: anything longer is not a name.
MAX_NAME_CHARS = 253

#: Bytes of a TCP payload examined for a request line and its Host header. The
#: request line is read to PROVE this end is the client and to find `Host:`; the
#: line itself is discarded and never leaves this function -- see
#: :func:`http_request`.
MAX_REQUEST_HEAD_BYTES = 2048

SOURCE_SNI = "tls-sni"
SOURCE_DNS = "dns"
SOURCE_HTTP = "http-plain"
SOURCE_IP = "ip-only"

#: Every source label this module can emit. The renderer asserts it can draw
#: each one: one producer, one meaning, every consumer.
SOURCES = (SOURCE_SNI, SOURCE_DNS, SOURCE_HTTP, SOURCE_IP)

PROTO_TLS = "tls"
PROTO_DNS = "dns"
PROTO_HTTP = "http"

#: What the page says about a TLS row, so nobody reads a host list as a URL
#: list. Emitted by the renderer whenever any row came from a ClientHello.
TLS_HIDES_THE_PATH = (
    "host-level only: this traffic is TLS, so the capture names the host a "
    "case reached and never the URL it requested"
)

_GLOBAL_LEN = 24
_RECORD_LEN = 16

_LINKTYPE_ETHERNET = 1
_LINKTYPE_RAW = 101
_LINKTYPE_RAW4 = 228
_LINKTYPE_RAW6 = 229

_ETH_IPV4 = 0x0800
_ETH_IPV6 = 0x86DD
_ETH_VLAN = 0x8100

_IPPROTO_TCP = 6
_IPPROTO_UDP = 17

_MAGIC_US = 0xA1B2C3D4
_MAGIC_NS = 0xA1B23C4D

_HTTP_METHODS = (
    b"GET ",
    b"POST ",
    b"PUT ",
    b"DELETE ",
    b"HEAD ",
    b"PATCH ",
    b"OPTIONS ",
)

_NAME_OK = set(string.ascii_lowercase + string.digits + ".-_")


def _hostname(raw: bytes) -> str:
    """A byte string as a host name, or "" when it is not one.

    Positive: every character must be legal in a name. A blob that happened to
    sit where a name goes is rejected rather than printed into a report.
    """
    try:
        text = raw.decode("ascii", errors="strict").strip().lower()
    except (UnicodeDecodeError, AttributeError):
        return ""
    if not text or len(text) > MAX_NAME_CHARS:
        return ""
    if any(char not in _NAME_OK for char in text):
        return ""
    if text.startswith(".") or ".." in text:
        return ""
    return text


def _ipv4(raw: bytes) -> str:
    return ".".join(str(byte) for byte in raw)


def _ipv6(raw: bytes) -> str:
    """An IPv6 address as text, with a mapped v4 address printed as v4.

    The guest uses both ``fec0::`` and ``::ffff:`` mapped forms, so a mapped
    address printed in its v6 spelling would be a SECOND name for a host the
    page already lists -- two rows for one conversation.
    """
    if raw[:10] == b"\x00" * 10 and raw[10:12] == b"\xff\xff":
        return _ipv4(raw[12:16])
    words = [(raw[i] << 8) | raw[i + 1] for i in range(0, 16, 2)]
    return ":".join("%x" % word for word in words)


def _endian(magic: bytes):
    """``(struct prefix, nanosecond timestamps)`` or None."""
    for prefix in ("<", ">"):
        value = struct.unpack(prefix + "I", magic)[0]
        if value == _MAGIC_US:
            return prefix, False
        if value == _MAGIC_NS:
            return prefix, True
    return None


def _tcp(src: str, dst: str, body: bytes):
    if len(body) < 20:
        return None
    offset = (body[12] >> 4) * 4
    if offset < 20 or len(body) < offset:
        return None
    flags = body[13]
    return {
        "transport": "tcp",
        "src": src,
        "dst": dst,
        "sport": (body[0] << 8) | body[1],
        "dport": (body[2] << 8) | body[3],
        "payload": body[offset:],
        # A SYN WITHOUT ACK is the one packet in a TCP conversation that can
        # only have come from the client. A SYN+ACK is the server answer and
        # proves the OPPOSITE, so the ACK bit is part of the predicate.
        "syn": bool(flags & 0x02) and not bool(flags & 0x10),
    }


def _udp(src: str, dst: str, body: bytes):
    if len(body) < 8:
        return None
    return {
        "transport": "udp",
        "src": src,
        "dst": dst,
        "sport": (body[0] << 8) | body[1],
        "dport": (body[2] << 8) | body[3],
        "payload": body[8:],
        "syn": False,
    }


def _ip(data: bytes, version: int):
    if version == 4:
        if len(data) < 20:
            return None
        header = (data[0] & 0x0F) * 4
        if header < 20 or len(data) < header:
            return None
        total = (data[2] << 8) | data[3]
        proto = data[9]
        src, dst = _ipv4(data[12:16]), _ipv4(data[16:20])
        body = data[header:total] if header < total <= len(data) else data[header:]
    else:
        if len(data) < 40:
            return None
        proto = data[6]
        length = (data[4] << 8) | data[5]
        src, dst = _ipv6(data[8:24]), _ipv6(data[24:40])
        body = data[40 : 40 + length] if length else data[40:]
    if proto == _IPPROTO_TCP:
        return _tcp(src, dst, body)
    if proto == _IPPROTO_UDP:
        return _udp(src, dst, body)
    return None


def _layers(frame: bytes, linktype: int):
    """One frame as ``{transport, src, dst, sport, dport, payload, syn}`` or None."""
    if linktype == _LINKTYPE_ETHERNET:
        if len(frame) < 14:
            return None
        etype = (frame[12] << 8) | frame[13]
        offset = 14
        while etype == _ETH_VLAN and len(frame) >= offset + 4:
            etype = (frame[offset + 2] << 8) | frame[offset + 3]
            offset += 4
        body = frame[offset:]
        if etype == _ETH_IPV4:
            return _ip(body, 4)
        if etype == _ETH_IPV6:
            return _ip(body, 6)
        return None
    if linktype in (_LINKTYPE_RAW, _LINKTYPE_RAW4, _LINKTYPE_RAW6):
        if not frame:
            return None
        version = frame[0] >> 4
        return _ip(frame, version) if version in (4, 6) else None
    return None


def sni_of(payload: bytes) -> str:
    """The server name in a TLS ClientHello that fits in ONE record, or "".

    A hello split across records is not reassembled: a name this parser did not
    see whole is a name it does not claim. Every length in the extension block
    is checked against the bytes that are actually there -- a extension length
    that points past the end returns "" rather than reading whatever follows,
    which is the shape a malformed capture uses to make a parser read on.
    """
    try:
        if len(payload) < 45 or payload[0] != 0x16:
            return ""
        record = payload[5 : 5 + ((payload[3] << 8) | payload[4])]
        if len(record) < 39 or record[0] != 0x01:
            return ""
        pos = 4 + 2 + 32
        if len(record) < pos + 1:
            return ""
        pos += 1 + record[pos]
        if len(record) < pos + 2:
            return ""
        pos += 2 + ((record[pos] << 8) | record[pos + 1])
        if len(record) < pos + 1:
            return ""
        pos += 1 + record[pos]
        if len(record) < pos + 2:
            return ""
        end = min(len(record), pos + 2 + ((record[pos] << 8) | record[pos + 1]))
        pos += 2
        while pos + 4 <= end:
            etype = (record[pos] << 8) | record[pos + 1]
            elen = (record[pos + 2] << 8) | record[pos + 3]
            pos += 4
            if pos + elen > end:
                return ""
            if etype == 0x0000:
                entry = record[pos : pos + elen]
                if len(entry) < 5:
                    return ""
                nlen = (entry[3] << 8) | entry[4]
                name = entry[5 : 5 + nlen]
                return _hostname(name) if len(name) == nlen else ""
            pos += elen
    except (IndexError, ValueError):  # pragma: no cover - the bounds above cover it
        return ""
    return ""


def dns_names(payload: bytes) -> list:
    """The names asked for by a well-formed DNS QUERY, or [].

    Positive identification, not a port test: the QR bit must say query, the
    opcode must be a standard query, there must be at least one question and NO
    answers, and every label must be a legal name. A response, a port-53 blob
    and a random UDP payload all fail one of those, so a name here was really
    asked for by this device.
    """
    if len(payload) < 12 or len(payload) > 4096:
        return []
    flags = (payload[2] << 8) | payload[3]
    if flags & 0x8000:
        return []
    if (flags >> 11) & 0x0F:
        return []
    questions = (payload[4] << 8) | payload[5]
    answers = (payload[6] << 8) | payload[7]
    authority = (payload[8] << 8) | payload[9]
    if questions < 1 or questions > 4 or answers or authority:
        return []
    pos = 12
    names = []
    for _ in range(questions):
        labels = []
        while True:
            if pos >= len(payload):
                return []
            size = payload[pos]
            pos += 1
            if size == 0:
                break
            # A query carries no compression pointer, so a length byte with the
            # top bits set is not a query this parser vouches for.
            if size > 63 or pos + size > len(payload):
                return []
            labels.append(payload[pos : pos + size])
            pos += size
        if pos + 4 > len(payload):
            return []
        pos += 4
        name = _hostname(b".".join(labels)) if labels else ""
        if not name:
            return []
        names.append(name)
    return names


def http_request(payload: bytes):
    """``{"host"}`` for a plaintext HTTP request line, or None.

    The request line is PARSED and then DROPPED: it proves this end is the
    client and it locates the ``Host:`` header, and neither of those needs the
    method or the path to survive the function.
    """
    head = payload[:MAX_REQUEST_HEAD_BYTES]
    if not head.startswith(_HTTP_METHODS):
        return None
    line = head.split(b"\r\n", 1)[0]
    parts = line.split(b" ")
    if len(parts) != 3 or not parts[2].startswith(b"HTTP/"):
        return None
    host = ""
    for raw in head.split(b"\r\n")[1:33]:
        if raw[:5].lower() == b"host:":
            host = _hostname(raw[5:].split(b":")[0].strip())
            break
    # THE REQUEST TARGET IS NOT KEPT. It was, capped at 200 characters and
    # passed through the query scrubber, and that was not safe: a scrubber keyed
    # on `name=value` cannot see a PATH. A target shaped like
    # `/api/v2/patients/<a national id>/records/<a treatment name>` carries both
    # of those with no key to match on, and this evidence is rendered into a
    # self-contained HTML report a tester emails to a colleague. There is no allowlist that makes an
    # arbitrary path safe, and the query half was leaking too: `\btoken\b` has
    # no word boundary to find in `access_token`, so every real-world token
    # spelling survived beside a correctly redacted `password=`, which is the
    # worst shape available -- the page LOOKS scrubbed.
    #
    # So an http-plain row now carries exactly what a TLS row carries: the HOST
    # it reached. The method and path are dropped at the parser, not masked
    # downstream, because a value that never enters cannot leak from a consumer
    # somebody adds later. `TLS_HIDES_THE_PATH` is now true of every row rather
    # than of most of them.
    return {"host": host}


def _client_evidence(layer: dict):
    """What in THIS packet proves the sender is the client. None when nothing does."""
    payload = layer["payload"]
    name = sni_of(payload)
    if name:
        return {"kind": "sni", "sni": name}
    if layer["transport"] == "udp":
        names = dns_names(payload)
        if names:
            return {"kind": "dns", "names": names}
    request = http_request(payload)
    if request:
        return {"kind": "http", "host": request["host"]}
    if layer["syn"]:
        return {"kind": "syn"}
    return None


def _key(layer: dict):
    ends = tuple(
        sorted([(layer["src"], layer["sport"]), (layer["dst"], layer["dport"])])
    )
    return (layer["transport"],) + ends


def _new_flow(key):
    return {
        "transport": key[0],
        "ends": key[1][0]
        + ":"
        + str(key[1][1])
        + " and "
        + key[2][0]
        + ":"
        + str(key[2][1]),
        "packets": 0,
        "bytes": 0,
        "first_ms": None,
        "last_ms": None,
        "client": None,
        "server": None,
        "sni": "",
        "http_host": "",
        "qnames": {},
    }


def _absorb(conv: dict, layer: dict) -> None:
    evidence = _client_evidence(layer)
    if evidence is None:
        return
    if conv["client"] is None:
        conv["client"] = (layer["src"], layer["sport"])
        conv["server"] = (layer["dst"], layer["dport"])
    kind = evidence["kind"]
    if kind == "sni":
        if not conv["sni"]:
            conv["sni"] = evidence["sni"]
    elif kind == "dns":
        for name in evidence["names"]:
            conv["qnames"][name] = conv["qnames"].get(name, 0) + 1
    elif kind == "http":
        if evidence["host"] and not conv["http_host"]:
            conv["http_host"] = evidence["host"]


def _merge(rows: dict, row: dict) -> None:
    key = (row["host"], row["port"], row["proto"], row["source"])
    held = rows.get(key)
    if held is None:
        rows[key] = row
        return
    held["connections"] += row["connections"]
    held["packets"] += row["packets"]
    if held["bytes"] is None or row["bytes"] is None:
        held["bytes"] = None
    else:
        held["bytes"] += row["bytes"]
    for name in ("first_ms", "last_ms"):
        mine, theirs = held[name], row[name]
        if theirs is None:
            continue
        if mine is None:
            held[name] = theirs
        else:
            held[name] = min(mine, theirs) if name == "first_ms" else max(mine, theirs)
    for port in row["local_ports"]:
        if port not in held["local_ports"]:
            held["local_ports"].append(port)


def _rows(flows: dict) -> list:
    merged: dict = {}
    for conv in flows.values():
        client, server = conv["client"], conv["server"]
        base = {
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
        if conv["qnames"]:
            for name, count in conv["qnames"].items():
                row = dict(base)
                row.update(
                    {
                        "host": name,
                        "port": server[1] if server else None,
                        "proto": PROTO_DNS,
                        "source": SOURCE_DNS,
                        "connections": count,
                        # NOT a share of the flow bytes: a per-name byte count
                        # is not a thing this capture measured, and a number
                        # nobody measured is worse on a page than a blank.
                        "bytes": None,
                    }
                )
                _merge(merged, row)
            continue
        row = dict(base)
        if conv["sni"]:
            row.update(
                {
                    "host": conv["sni"],
                    "port": server[1],
                    "proto": PROTO_TLS,
                    "source": SOURCE_SNI,
                }
            )
        elif conv["http_host"]:
            # KEYED ON THE HOST, not on a list of request lines. It was `elif
            # conv["targets"]`, and dropping the request target would have made
            # this branch unreachable -- every plaintext row would have fallen
            # through to ip-only and the feature would have silently stopped
            # naming hosts it had correctly parsed.
            row.update(
                {
                    "host": conv["http_host"],
                    "port": server[1],
                    "proto": PROTO_HTTP,
                    "source": SOURCE_HTTP,
                }
            )
        elif server is not None:
            row.update(
                {
                    "host": server[0],
                    "port": server[1],
                    "proto": conv["transport"],
                    "source": SOURCE_IP,
                }
            )
        else:
            # NOTHING in this flow identified a client, so neither end is the
            # server. Said, not guessed -- and with no local port, so the owner
            # attribution can only report it unsampled.
            row.update(
                {
                    "host": conv["ends"],
                    "port": None,
                    "proto": conv["transport"],
                    "source": SOURCE_IP,
                    "local_ports": [],
                    "undetermined": True,
                }
            )
        _merge(merged, row)
    return sorted(
        merged.values(),
        key=lambda item: (-int(item["connections"]), str(item["host"])),
    )


def parse(data: object) -> dict:
    """A libpcap file as rows. ``{"error", "content": {...}}``. Never raises.

    ``error`` is set only when the input is not a capture at all. Everything
    else -- an unreadable link type, a lying record length, a cap that bound --
    is a successful parse whose content SAYS so, because "we read it and there
    was nothing" and "we could not read it" are the two facts a report must not
    conflate.
    """
    try:
        raw = bytes(data or b"")
    except (TypeError, ValueError):
        return {"error": "the capture was not readable as bytes", "content": None}
    truncated = len(raw) > MAX_PCAP_BYTES
    if truncated:
        raw = raw[:MAX_PCAP_BYTES]
    if len(raw) < _GLOBAL_LEN:
        return {
            "error": "the capture file is shorter than a libpcap file header",
            "content": None,
        }
    head = _endian(raw[:4])
    if head is None:
        return {
            "error": "the capture file carries no libpcap magic number",
            "content": None,
        }
    prefix, nanos = head
    linktype = struct.unpack(prefix + "I", raw[20:24])[0]
    supported = linktype in (
        _LINKTYPE_ETHERNET,
        _LINKTYPE_RAW,
        _LINKTYPE_RAW4,
        _LINKTYPE_RAW6,
    )
    flows: dict = {}
    dropped_flows = 0
    packets = 0
    total = 0
    unparsed = 0
    pos = _GLOBAL_LEN
    while pos + _RECORD_LEN <= len(raw) and packets < MAX_PACKETS:
        stamp_a, stamp_b, incl, _orig = struct.unpack(
            prefix + "IIII", raw[pos : pos + _RECORD_LEN]
        )
        pos += _RECORD_LEN
        if incl > len(raw) - pos:
            truncated = True
            break
        frame = raw[pos : pos + incl]
        pos += incl
        packets += 1
        total += incl
        stamp = stamp_a * 1000 + (stamp_b // 1000000 if nanos else stamp_b // 1000)
        if not supported:
            unparsed += 1
            continue
        try:
            layer = _layers(frame, linktype)
        except Exception:  # pragma: no cover - the bounds above cover it
            layer = None
        if layer is None:
            unparsed += 1
            continue
        key = _key(layer)
        conv = flows.get(key)
        if conv is None:
            if len(flows) >= MAX_FLOWS:
                dropped_flows += 1
                continue
            conv = _new_flow(key)
            flows[key] = conv
        conv["packets"] += 1
        conv["bytes"] += incl
        conv["first_ms"] = (
            stamp if conv["first_ms"] is None else min(conv["first_ms"], stamp)
        )
        conv["last_ms"] = (
            stamp if conv["last_ms"] is None else max(conv["last_ms"], stamp)
        )
        _absorb(conv, layer)
    if pos + _RECORD_LEN <= len(raw):
        truncated = True
    notes = []
    if not supported:
        notes.append(
            "the capture link type is " + str(linktype) + ", which this parser "
            "does not read, so no flow was extracted from it"
        )
    if truncated:
        notes.append("the capture was longer than this parser reads, so it is partial")
    if dropped_flows:
        notes.append(
            str(dropped_flows)
            + " flow(s) past the "
            + str(MAX_FLOWS)
            + "-flow limit were not counted"
        )
    return {
        "error": None,
        "content": {
            "rows": _rows(flows),
            "packets": packets,
            "bytes": total,
            "flows": len(flows),
            "dropped_flows": dropped_flows,
            "unparsed": unparsed,
            "truncated": truncated,
            "linktype": int(linktype),
            "note": "; ".join(notes),
        },
    }
