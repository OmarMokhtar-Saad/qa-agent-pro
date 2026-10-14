"""A byte string as a host name."""

from __future__ import annotations

import string

from tools.mobile_evidence import pcap_const

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
    if not text or len(text) > pcap_const.MAX_NAME_CHARS:
        return ""
    if any(char not in _NAME_OK for char in text):
        return ""
    if text.startswith(".") or ".." in text:
        return ""
    return text
