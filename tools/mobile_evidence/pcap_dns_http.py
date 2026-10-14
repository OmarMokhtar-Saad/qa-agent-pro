"""Names carried in cleartext: DNS queries and an HTTP Host header."""

from __future__ import annotations

from tools.mobile_evidence.pcap_const import _HTTP_METHODS, MAX_REQUEST_HEAD_BYTES
from tools.mobile_evidence.pcap_host import _hostname


def _query_count(payload: bytes) -> int:
    """The question count of a well-formed DNS query header, or 0 if invalid."""
    if len(payload) < 12 or len(payload) > 4096:
        return 0
    flags = (payload[2] << 8) | payload[3]
    if flags & 0x8000:
        return 0
    if (flags >> 11) & 0x0F:
        return 0
    questions = (payload[4] << 8) | payload[5]
    answers = (payload[6] << 8) | payload[7]
    authority = (payload[8] << 8) | payload[9]
    if questions < 1 or questions > 4 or answers or authority:
        return 0
    return questions


def _read_qname(payload: bytes, pos: int):
    """``(labels, pos after the name)`` read at ``pos``, or None if malformed."""
    labels: list[bytes] = []
    while True:
        if pos >= len(payload):
            return None
        size = payload[pos]
        pos += 1
        if size == 0:
            return labels, pos
        # A query carries no compression pointer, so a length byte with the
        # top bits set is not a query this parser vouches for.
        if size > 63 or pos + size > len(payload):
            return None
        labels.append(payload[pos : pos + size])
        pos += size


def dns_names(payload: bytes) -> list:
    """The names asked for by a well-formed DNS QUERY, or [].

    Positive identification, not a port test: the QR bit must say query, the
    opcode must be a standard query, there must be at least one question and NO
    answers, and every label must be a legal name. A response, a port-53 blob
    and a random UDP payload all fail one of those, so a name here was really
    asked for by this device.
    """
    questions = _query_count(payload)
    if not questions:
        return []
    return _query_names(payload, questions)


def _query_names(payload: bytes, questions: int) -> list:
    """The names of ``questions`` questions read from ``payload``, or []."""
    pos = 12
    names = []
    for _ in range(questions):
        parsed = _read_qname(payload, pos)
        if parsed is None:
            return []
        labels, pos = parsed
        if pos + 4 > len(payload):
            return []
        pos += 4
        name = _hostname(b".".join(labels)) if labels else ""
        if not name:
            return []
        names.append(name)
    return names


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
def http_request(payload: bytes):
    """``{"host"}`` for a plaintext HTTP request line, or None.

    The request line is PARSED and then DROPPED: it proves this end is the
    client and it locates the ``Host:`` header, and neither of those needs the
    method or the path to survive the function.
    """
    head = payload[:MAX_REQUEST_HEAD_BYTES]
    if not _is_request(head):
        return None
    return {"host": _host_header(head)}


def _is_request(head: bytes) -> bool:
    """True when ``head`` opens with an HTTP request line."""
    if not head.startswith(_HTTP_METHODS):
        return False
    line = head.split(b"\r\n", 1)[0]
    parts = line.split(b" ")
    return len(parts) == 3 and parts[2].startswith(b"HTTP/")


def _host_header(head: bytes) -> str:
    """The name in the first ``Host:`` header of ``head``, or ""."""
    for raw in head.split(b"\r\n")[1:33]:
        if raw[:5].lower() == b"host:":
            return _hostname(raw[5:].split(b":")[0].strip())
    return ""
