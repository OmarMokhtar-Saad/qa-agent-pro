"""Link and IP layers of one captured frame."""

from __future__ import annotations

from tools.mobile_evidence.pcap_const import (
    _ETH_IPV4,
    _ETH_IPV6,
    _ETH_VLAN,
    _IPPROTO_TCP,
    _IPPROTO_UDP,
    _LINKTYPE_ETHERNET,
    _LINKTYPE_RAW,
    _LINKTYPE_RAW4,
    _LINKTYPE_RAW6,
)


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


def _ip4(data: bytes):
    """``(proto, src, dst, body)`` of an IPv4 packet, or None."""
    if len(data) < 20:
        return None
    header = (data[0] & 0x0F) * 4
    if header < 20 or len(data) < header:
        return None
    total = (data[2] << 8) | data[3]
    body = data[header:total] if header < total <= len(data) else data[header:]
    return data[9], _ipv4(data[12:16]), _ipv4(data[16:20]), body


def _ip6(data: bytes):
    """``(proto, src, dst, body)`` of an IPv6 packet, or None."""
    if len(data) < 40:
        return None
    length = (data[4] << 8) | data[5]
    body = data[40 : 40 + length] if length else data[40:]
    return data[6], _ipv6(data[8:24]), _ipv6(data[24:40]), body


def _ip(data: bytes, version: int):
    parsed = _ip4(data) if version == 4 else _ip6(data)
    if parsed is None:
        return None
    proto, src, dst, body = parsed
    if proto == _IPPROTO_TCP:
        return _tcp(src, dst, body)
    if proto == _IPPROTO_UDP:
        return _udp(src, dst, body)
    return None


def _eth_body(frame: bytes):
    """The IP layers of an Ethernet frame (VLAN tags skipped), or None."""
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


def _raw_body(frame: bytes):
    """The IP layers of a raw-IP frame, or None."""
    if not frame:
        return None
    version = frame[0] >> 4
    return _ip(frame, version) if version in (4, 6) else None


def _layers(frame: bytes, linktype: int):
    """One frame as ``{transport, src, dst, sport, dport, payload, syn}`` or None."""
    if linktype == _LINKTYPE_ETHERNET:
        return _eth_body(frame)
    if linktype in (_LINKTYPE_RAW, _LINKTYPE_RAW4, _LINKTYPE_RAW6):
        return _raw_body(frame)
    return None
