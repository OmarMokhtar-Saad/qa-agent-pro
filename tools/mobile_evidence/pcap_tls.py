"""The server name of a TLS ClientHello."""

from __future__ import annotations

from tools.mobile_evidence.pcap_host import _hostname


def sni_of(payload: bytes) -> str:
    """The server name in a TLS ClientHello that fits in ONE record, or "".

    A hello split across records is not reassembled: a name this parser did not
    see whole is a name it does not claim. Every length in the extension block
    is checked against the bytes that are actually there -- a extension length
    that points past the end returns "" rather than reading whatever follows,
    which is the shape a malformed capture uses to make a parser read on.
    """
    try:
        record = _client_hello(payload)
        if record is None:
            return ""
        span = _extension_span(record)
        if span is None:
            return ""
        return _server_name(record, span[0], span[1])
    except (IndexError, ValueError):  # pragma: no cover - the bounds above cover it
        return ""


def _client_hello(payload: bytes):
    """The handshake record of a one-record ClientHello, or None."""
    if len(payload) < 45 or payload[0] != 0x16:
        return None
    record = payload[5 : 5 + ((payload[3] << 8) | payload[4])]
    if len(record) < 39 or record[0] != 0x01:
        return None
    return record


def _extension_span(record: bytes):
    """``(start, end)`` of the extension block inside ``record``, or None."""
    pos = 4 + 2 + 32
    if len(record) < pos + 1:
        return None
    pos += 1 + record[pos]
    if len(record) < pos + 2:
        return None
    pos += 2 + ((record[pos] << 8) | record[pos + 1])
    if len(record) < pos + 1:
        return None
    pos += 1 + record[pos]
    if len(record) < pos + 2:
        return None
    end = min(len(record), pos + 2 + ((record[pos] << 8) | record[pos + 1]))
    return pos + 2, end


def _server_name(record: bytes, pos: int, end: int) -> str:
    """The host name of the server_name extension in ``record[pos:end]``, or ""."""
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
    return ""
