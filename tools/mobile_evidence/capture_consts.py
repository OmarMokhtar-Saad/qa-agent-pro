"""The caps, names and reason strings of the app-evidence capture, in one place.

Every other ``capture_*`` module reads these as ``_consts.NAME`` at call time, so
a test that patches a cap here bites in its one reader. The ``capture`` facade
holds a read-only copy of each value: patch THIS module, not the facade.
"""

from __future__ import annotations

import re

EVIDENCE_FLAG_NAME = "QA_MOBILE_APP_EVIDENCE"

#: One slice: the reference's per-turn slices were tens of KB; a 4 MiB cap is
#: hundreds of times that and the same cap the screen dump has.
MAX_SLICE_BYTES = 4 * 1024 * 1024

#: One slice from the GENERIC profile, which has no logcat tag and is therefore
#: narrowed by pid alone. A quarter of the profiled cap, because a dump nobody
#: filtered by tag is broader and its value per byte is lower.
MAX_GENERIC_SLICE_BYTES = 1 * 1024 * 1024

#: The whole pulled event log for one run.
MAX_EVENTS_BYTES = 4 * 1024 * 1024

#: How old a capture file in the AVD directory must be before a LATER case
#: deletes it as abandoned. It is not a guess about age but about OWNERSHIP: a
#: case that is still running holds its file for the length of one tool call,
#: and an hour is far longer than any of those, so a file this old belongs to a
#: chat that closed. Deleting a live case's file would cost that case its
#: evidence, which is why the bound is generous in that direction.
ORPHAN_CAPTURE_TTL_S = 3600.0

EVENTS_FILE = "events.ndjson"

#: What the case carries when the device clock could not be read (plan D6).
CLOCK_NOT_READ = "device clock not read -- attribution by host clock, ±2 s"

_SEGMENT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,120}$")

#: The memory is BOUNDED, because "abandoned" is a state the lane computes from a
#: stale lease, not an event it could hook: a run whose chat was closed never
#: reaches ``pull_events`` and would otherwise keep its typed values in this
#: process until restart. Oldest run first out. A run evicted before its pull is
#: not armed from memory at pull time -- the key and prose nets still apply --
#: and docs/MOBILE_TESTING.md states the limit.
MAX_REMEMBERED_RUNS = 8

# THE GENERIC PROFILE HAS NO TAG, so the pid is the ONLY thing narrowing the
# dump. Without it `logcat -d` carries neither `--pid` nor `-s` and a megabyte
# of every app on the device is written into this run's evidence and shown in
# its report -- confirmed by a review that ran it. A real profile still has its
# tag, so this bound is on the generic path alone.
_UNSCOPED_GENERIC_REASON = (
    "the app was not running when the slice was taken, so "
    "its log could not be told apart from every other "
    "app's; nothing was captured"
)

#: Samples of the socket table taken while one case replays. Sockets are short
#: lived, so this is the resolution of the owner column: at one per second it
#: covers the longest case the lane will run. Each sample is two short adb
#: reads, so the cost is bounded by this number and paid off the packet path.
MAX_NETWORK_SAMPLES = 600

#: Seconds between two samples of the socket table.
NETWORK_SAMPLE_INTERVAL_S = 1.0

#: Rows of the parsed summary kept for one case. The parser already bounds
#: flows; this bounds what reaches the checkpoint and the page, where a tester
#: reads. Past a couple of hundred hosts the section is not a report.
MAX_NETWORK_ROWS = 200

#: Cases whose sampler may be running at once. The lane runs one case at a
#: time, so anything above one is slack for a case that ended without its
#: finish being called; the oldest is cancelled to make room, never left.
MAX_LIVE_SAMPLERS = 4

NETWORK_NAME_PREFIX = "qa-net-"

#: What a record says when its case never reached a replay.
ABANDONED_STAGE = "abandoned"
ABANDONED_REASON = (
    "the case was abandoned before its first replay, so the capture was stopped "
    "and discarded unread"
)
