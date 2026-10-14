"""Walking the packet records of a libpcap file."""

from __future__ import annotations

import struct
from dataclasses import dataclass

from config.settings_guard import never_raise
from tools.mobile_evidence.pcap_const import (
    _GLOBAL_LEN,
    _MAGIC_NS,
    _MAGIC_US,
    _RECORD_LEN,
    _SUPPORTED_LINKTYPES,
)
from tools.mobile_evidence.pcap_flows import _absorb, _count_packet, _key, _new_flow
from tools.mobile_evidence.pcap_ip import _layers


def _endian(magic: bytes):
    """``(struct prefix, nanosecond timestamps)`` or None."""
    for prefix in ("<", ">"):
        value = struct.unpack(prefix + "I", magic)[0]
        if value == _MAGIC_US:
            return prefix, False
        if value == _MAGIC_NS:
            return prefix, True
    return None


def _frame_layer(frame: bytes, linktype: int):
    """The decoded layers of one frame, or None when it cannot be read."""
    # the bounds above cover it
    with never_raise():
        return _layers(frame, linktype)
    return None


@dataclass(frozen=True)
class Limits:
    """The three bounds one parse runs under."""

    max_bytes: int
    max_packets: int
    max_flows: int


@dataclass(frozen=True)
class _Header:
    """The libpcap file header: byte order, clock unit, link type."""

    prefix: str
    nanos: bool
    linktype: int
    supported: bool


def _header(raw: bytes) -> tuple[_Header | None, str | None]:
    """``(header, None)`` or ``(None, why this is not a capture)``."""
    if len(raw) < _GLOBAL_LEN:
        return None, "the capture file is shorter than a libpcap file header"
    found = _endian(raw[:4])
    if found is None:
        return None, "the capture file carries no libpcap magic number"
    prefix, nanos = found
    linktype = struct.unpack(prefix + "I", raw[20:24])[0]
    return _Header(prefix, nanos, linktype, linktype in _SUPPORTED_LINKTYPES), None


def _stamp_ms(stamp_a: int, stamp_b: int, nanos: bool) -> int:
    """A record timestamp (seconds, sub-seconds) as milliseconds."""
    return stamp_a * 1000 + (stamp_b // 1000000 if nanos else stamp_b // 1000)


class _Scan:
    """Walk the packet records: flows plus the counters of one read.

    ``cut`` is True when a record lied about its length or records were left unread.
    """

    def __init__(self, raw: bytes, head: _Header, limits: Limits) -> None:
        self.raw = raw
        self.head = head
        self.limits = limits
        self.flows: dict = {}
        self.packets = 0
        self.total = 0
        self.unparsed = 0
        self.dropped = 0
        self.cut = False

    def run(self) -> _Scan:
        """Read every record the bounds allow."""
        pos = _GLOBAL_LEN
        room = self.limits.max_packets
        while pos + _RECORD_LEN <= len(self.raw) and self.packets < room:
            step = self._step(pos)
            if step is None:
                return self
            pos = step
        if pos + _RECORD_LEN <= len(self.raw):
            self.cut = True
        return self

    def _step(self, pos: int) -> int | None:
        """Read the record at ``pos``; the next position, or None on a lie."""
        fields = self.raw[pos : pos + _RECORD_LEN]
        stamp_a, stamp_b, incl, _orig = struct.unpack(self.head.prefix + "IIII", fields)
        pos += _RECORD_LEN
        if incl > len(self.raw) - pos:
            self.cut = True
            return None
        stamp = _stamp_ms(stamp_a, stamp_b, self.head.nanos)
        self._take(self.raw[pos : pos + incl], incl, stamp)
        return pos + incl

    def _take(self, frame: bytes, incl: int, stamp: int) -> None:
        """Count one frame and fold its layers into its flow."""
        self.packets += 1
        self.total += incl
        layer = None
        if self.head.supported:
            layer = _frame_layer(frame, self.head.linktype)
        if layer is None:
            self.unparsed += 1
            return
        conv = self._conv(_key(layer))
        if conv is None:
            return
        _count_packet(conv, incl, stamp)
        _absorb(conv, layer)

    def _conv(self, key: tuple) -> dict | None:
        """The flow of ``key``, a new one, or None once the flow cap binds."""
        conv = self.flows.get(key)
        if conv is None:
            if len(self.flows) >= self.limits.max_flows:
                self.dropped += 1
                return None
            conv = _new_flow(key)
            self.flows[key] = conv
        return conv


def _read_records(raw: bytes, head: _Header, limits: Limits) -> _Scan:
    """The records of ``raw`` read under ``limits``."""
    return _Scan(raw, head, limits).run()
