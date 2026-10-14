"""The names and numbers of a libpcap file, shared by every pcap leaf."""

from __future__ import annotations

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
_SUPPORTED_LINKTYPES = (
    _LINKTYPE_ETHERNET,
    _LINKTYPE_RAW,
    _LINKTYPE_RAW4,
    _LINKTYPE_RAW6,
)

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
