"""Labels of the socket-table evidence: the source, the owner states, the notes.

Pure constants; ``netattr`` re-exports every one of them.
"""

from __future__ import annotations

#: What a row from the socket table says it is. The parser's own labels live in
#: ``pcap.SOURCES``; this one has a different producer, so it is named here.
SOURCE_SOCKET = "socket-table"

#: What the page says about a socket-table row, so nobody reads an address as a
#: host that could not be named. Stated once, here, because the reason is a
#: property of the SOURCE and not of any one row.
SOCKET_NOTE = (
    "rows named socket-table come from the device's own socket list, which "
    "records the address a connection went to and never the name asked for or "
    "the path requested"
)

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
