"""Did the app under test die during this replay? One logcat slice, judged.

A mobile case used to be reported as PASS while the app under test had crashed:
the lane captured the slice (``tools/mobile_evidence/capture.slice_case``) and
then nothing ever read it. This module is the reader. It is PURE -- text in, a
record out, no device, no store, no settings -- so the judgement is graded on
fixtures rather than on a device.

**It reports a crash of the PACKAGE UNDER TEST and nothing else.** A slice is not
proof of ownership: the profiled capture narrows ``logcat`` by TAG, not by pid,
so another process's death legitimately sits in this case's slice. Attribution is
therefore EXPLICIT per kind, and never inferred from "the slice is ours":

* ``app_crash``     -- ``AndroidRuntime: FATAL EXCEPTION``, attributed by the
  runtime's own following ``Process: <pkg>`` line;
* ``native_abort``  -- ``DEBUG``/``libc`` ``Fatal signal`` or a ``tombstone``,
  attributed by debuggerd's own ``>>> <pkg> <<<``;
* ``anr``           -- ``ActivityManager: ANR in <pkg>``, attributed on the
  marker line itself;
* ``process_death`` -- ``ActivityManager`` reporting the process died or an
  activity was force-finished, attributed on the marker line itself.

FOUR CLAUSES decide a fire, and each one rejects an impostor no other clause
would -- the fixtures in ``tests/mobile_evidence/test_crash_detector.py`` are
named for them, because a clause two clauses would both cover is a clause nobody
graded:

1. a marker must be present at all -- app output that merely contains the word
   "crash" is not a crash;
2. the marker line must carry the SYSTEM tag that owns that marker -- an app
   logging the words ``FATAL EXCEPTION`` under its own tag is quoting, not dying;
3. the package must be named FOR the marker: on the line, or within
   :data:`MAX_ATTRIBUTION_LINES` parsed lines after it -- another process's crash
   fifty lines above an unrelated mention of our package is not our crash;
4. the name must match as a WHOLE package -- ``com.example.app.debug`` is a
   different app from ``com.example.app``.

Never raises, and never claims a crash it cannot attribute: everything else is
``detected: False`` with a ``reason`` naming the clause that said no. Silence is
not a clean bill of health, so the reason is always populated.

The text arrives ALREADY SCRUBBED by ``capture`` (the tester's typed values are
masked before anything reaches disk, and this module is called after that), and
the marker and excerpt are neutralised of prompt-guard markers HERE, because both
are stored on the case record and rendered into the HTML report -- and neither of
those paths goes through ``wrap_untrusted``. The markers come from
``tools/untrusted`` (the same derivation ``perception`` uses) rather than being
copied, for the reason stated in ``perception._guard_sentinel``: a copy of a
guard phrase drifts silently. The
CHAT path, which does reach a model, wraps them: ``tools/mobile/render.crash_note``.
"""

from __future__ import annotations

import logging
import re  # noqa: F401

from tools.mobile_evidence.crash_const import (  # noqa: F401
    _CONTROL_RE,
    _LINE_RE,
    _TAGS_AM,
    _TAGS_CRASH,
    _TAGS_NATIVE,
    _WS_RE,
    KIND_ANR,
    KIND_APP_CRASH,
    KIND_LABELS,
    KIND_NATIVE_ABORT,
    KIND_PROCESS_DEATH,
    KINDS,
    MAX_ATTRIBUTION_LINES,
    MAX_EXCERPT_CHARS,
    MAX_EXCERPT_LINES,
    MAX_MARKER_CHARS,
    MAX_SCAN_BYTES,
    SERVER_PREFIX,
)
from tools.mobile_evidence.crash_match import (  # noqa: F401
    _attributes,
    _find_marker,
    _kind_of,
    _parse_lines,
)
from tools.mobile_evidence.crash_scan import scan  # noqa: F401
from tools.mobile_evidence.crash_server import (  # noqa: F401
    crash_of_case,
    server_reason,
)
from tools.mobile_evidence.crash_text import (  # noqa: F401
    _empty,
    _excerpt,
    _missed,
    _neutralize,
    _package_re,
)
from tools.untrusted import GUARD_MARKERS, NEUTRALIZED  # noqa: F401

logger = logging.getLogger(__name__)
