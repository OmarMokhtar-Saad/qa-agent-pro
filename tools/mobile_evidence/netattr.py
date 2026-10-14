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
from typing import Any

from tools.mobile_evidence.netattr_labels import (  # noqa: F401
    NO_UID_NOTE,
    OWNER_APP,
    OWNER_OTHER,
    OWNER_UNSAMPLED,
    OWNERS,
    SOCKET_NOTE,
    SOURCE_SOCKET,
)
from tools.mobile_evidence.netattr_merge import (  # noqa: F401
    EndpointSample,
    _connected,
    endpoints_capped,
    merge_capped,
)
from tools.mobile_evidence.netattr_proc import (  # noqa: F401
    _UID_RE,
    _hex_port,
    _ip_from_hex,
    app_uid,
    parse_rows,
)
from tools.mobile_evidence.netattr_rows import (  # noqa: F401
    attribute,
    endpoint_rows,
    merge_rows,
    owner_of,
)

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

#: Distinct endpoints remembered for one case. A case reaches a handful of
#: hosts and the system image adds its own; past this a NEW endpoint is dropped
#: and counted rather than silently merged, the same rule the packet parser
#: uses for flows, and for the same reason: a page nobody can read is not a
#: report.
MAX_TRACKED_ENDPOINTS = 400


def parse_proc_net(text: object, proto: str = "tcp") -> list:
    """One ``/proc/net`` socket table as rows, at most MAX_PROC_LINES lines."""
    return parse_rows(text, proto, MAX_PROC_LINES)


def merge_samples(samples: object, rows: Any) -> dict:
    """Fold one sample into ``{local_port: [uid, ...]}``."""
    return merge_capped(samples, rows, MAX_SAMPLED_PORTS)


def merge_endpoints(store: object, sample: EndpointSample) -> dict:
    """Fold one sample's rows into the app's endpoint map."""
    return endpoints_capped(store, sample, MAX_TRACKED_ENDPOINTS)
