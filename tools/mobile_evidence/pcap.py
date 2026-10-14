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

from tools.mobile_evidence.pcap_const import (  # noqa: F401
    MAX_NAME_CHARS,
    MAX_REQUEST_HEAD_BYTES,
    PROTO_DNS,
    PROTO_HTTP,
    PROTO_TLS,
    SOURCE_DNS,
    SOURCE_HTTP,
    SOURCE_IP,
    SOURCE_SNI,
    SOURCES,
    TLS_HIDES_THE_PATH,
)
from tools.mobile_evidence.pcap_dns_http import dns_names, http_request  # noqa: F401
from tools.mobile_evidence.pcap_host import _hostname  # noqa: F401
from tools.mobile_evidence.pcap_read import Limits
from tools.mobile_evidence.pcap_result import parse_with
from tools.mobile_evidence.pcap_tls import sni_of  # noqa: F401

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


def parse(data: object) -> dict:
    """A libpcap file as rows. ``{"error", "content": {...}}``. Never raises.

    ``error`` is set only when the input is not a capture at all. Everything
    else -- an unreadable link type, a lying record length, a cap that bound --
    is a successful parse whose content SAYS so, because "we read it and there
    was nothing" and "we could not read it" are the two facts a report must not
    conflate.
    """
    return parse_with(data, Limits(MAX_PCAP_BYTES, MAX_PACKETS, MAX_FLOWS))
