"""Reading the device: the app uid and one ``/proc/net`` table as rows.

Pure and never raising: text in, records out. ``netattr`` re-exports these and
passes the line cap in, because that cap is patched on ``netattr``.
"""

from __future__ import annotations

import re

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


def _octets(raw: str) -> bytes | None:
    """The address words read back to front, or None when the text is not hex."""
    octets: list = []
    try:
        for start in range(0, len(raw), 8):
            octets.extend(reversed(bytes.fromhex(raw[start : start + 8])))
    except ValueError:
        return None
    return bytes(octets)


def _format_ip(data: bytes) -> str:
    """Four bytes as a v4, a v4-mapped v6 as its v4, anything else as a v6."""
    if len(data) == 4:
        return ".".join(str(byte) for byte in data)
    if data[:10] == b"\x00" * 10 and data[10:12] == b"\xff\xff":
        return ".".join(str(byte) for byte in data[12:16])
    return ":".join("%x" % ((data[i] << 8) | data[i + 1]) for i in range(0, 16, 2))


# One ``/proc/net`` address as text, or ``""``.
#
# The kernel prints the address as 32-bit words in HOST order, so each group
# of eight hex characters is four bytes to be read backwards. A v4-mapped v6
# address is printed as the v4 it is, for the same reason the packet parser
# does it: one host must not become two rows on the page.
def _ip_from_hex(text: object) -> str:
    raw = str(text or "").strip()
    if len(raw) not in (8, 32):
        return ""
    data = _octets(raw)
    return "" if data is None else _format_ip(data)


def _local_uid(fields: list) -> tuple[int, int] | None:
    """The local port and uid of one table line, or None for a line to skip."""
    if len(fields) < 8 or not fields[0].endswith(":"):
        return None
    local = fields[1].split(":")
    if len(local) != 2:
        return None
    port = _hex_port(local[1])
    if port is None:
        return None
    try:
        uid = int(fields[7])
    except (TypeError, ValueError, OverflowError):
        return None
    return (port, uid) if uid >= 0 else None


def _row(fields: list, proto: str) -> dict | None:
    """One socket row from the fields of a table line, or None."""
    got = _local_uid(fields)
    if got is None:
        return None
    remote = str(fields[2]).split(":")
    return {
        "local_port": got[0],
        "uid": got[1],
        "state": str(fields[3])[:4],
        "remote_ip": _ip_from_hex(remote[0]) if len(remote) == 2 else "",
        "remote_port": _hex_port(remote[1]) if len(remote) == 2 else None,
        "proto": str(proto or "tcp")[:8],
    }


# One ``/proc/net`` socket table as rows.
#
# ``[{"local_port", "uid", "state", "remote_ip", "remote_port", "proto"}]``.
# The remote end is read HERE because this table is the only place a real
# device will say who the app talked to; *proto* is passed in rather than
# guessed from the text, because the four tables are identical in shape and
# only the caller knows which one it read.
#
# A malformed line is SKIPPED, never raised on and never counted as a socket:
# this text comes off a device, and one unreadable row must not cost the case
# every row after it. The header line is skipped the same way, by failing the
# same checks rather than by a special case -- one rule, not two.
def parse_rows(text: object, proto: str, limit: int) -> list:
    """One ``/proc/net`` socket table as rows, reading at most *limit* lines."""
    rows = []
    for index, line in enumerate(str(text or "").splitlines()):
        if index >= limit:
            break
        row = _row(line.split(), proto)
        if row is not None:
            rows.append(row)
    return rows
