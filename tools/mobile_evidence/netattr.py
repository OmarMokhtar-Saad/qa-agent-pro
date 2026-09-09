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


def parse_proc_net(text: object) -> list:
    """``/proc/net/tcp`` or ``tcp6`` as ``[{"local_port", "uid", "state"}]``.

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
        rows.append({"local_port": port, "uid": uid, "state": str(fields[3])[:4]})
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
