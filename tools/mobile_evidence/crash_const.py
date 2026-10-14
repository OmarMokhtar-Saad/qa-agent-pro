"""Crash detector constants: kinds, bounds and the logcat line patterns."""

from __future__ import annotations

import re

KIND_APP_CRASH = "app_crash"
KIND_NATIVE_ABORT = "native_abort"
KIND_ANR = "anr"
KIND_PROCESS_DEATH = "process_death"

#: Every kind this module can report. Frozen as a tuple so a consumer can branch
#: on the whole set rather than on a literal it copied.
KINDS: tuple[str, ...] = (
    KIND_APP_CRASH,
    KIND_NATIVE_ABORT,
    KIND_ANR,
    KIND_PROCESS_DEATH,
)

#: How each kind reads to a tester who does not read logcat. ONE producer of the
#: phrase, so the chat reply, the verdict reason and the report cannot word the
#: same event three ways.
KIND_LABELS: dict[str, str] = {
    KIND_APP_CRASH: "the app threw an unhandled exception and was killed",
    KIND_NATIVE_ABORT: "the app aborted in native code",
    KIND_ANR: "the app stopped responding (ANR)",
    KIND_PROCESS_DEATH: "the app's process died while the case was running",
}

#: Bytes of one slice this detector will read. It must be able to see everything
#: ``capture.MAX_SLICE_BYTES`` admits, or a crash inside a legally captured slice
#: is invisible -- so it is set to the same value rather than lower. Text beyond
#: it keeps the TAIL, never the head: a crash ENDS the process, so it is at the
#: end of a slice, and a head-clipping scan would systematically miss the one
#: event this module exists to find.
MAX_SCAN_BYTES = 4 * 1024 * 1024

#: Parsed lines after a marker in which the package must be named for the marker
#: to count. The harmful direction is UPWARD: a wide window attributes ANOTHER
#: process's death to the app under test, which is a false FAIL -- the failure
#: mode this module creates and must not manufacture. Four is what the runtime
#: itself writes: ``FATAL EXCEPTION`` is followed by ``Process:`` within two.
MAX_ATTRIBUTION_LINES = 4

#: Lines of the excerpt kept as evidence.
MAX_EXCERPT_LINES = 12

#: Characters of that excerpt -- the second half of the same bound, because a
#: stack frame line is long. It also bounds what ``render.crash_note`` hands a
#: model inside its untrusted wrapper.
MAX_EXCERPT_CHARS = 1200

#: Characters of the ONE line offered as proof. It is quoted in the verdict
#: REASON, which ``case_runner._checkpoint`` caps at 1200 and
#: ``render.verdict_line`` clips at 160, so a large value silently pushes the
#: server's own sentence out of the tester's chat.
MAX_MARKER_CHARS = 200

#: What the reason says first, so a 160-character clip still carries the
#: attribution. The server made this call; the model did not.
SERVER_PREFIX = "Server override:"

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WS_RE = re.compile(r"[ \t]{2,}")

#: ``MM-DD HH:MM:SS.mmm  PID  TID L TAG: message`` -- logcat's threadtime format,
#: which is what ``adb logcat -d`` produces with and without ``-s``. A line that
#: does not parse cannot carry a marker: clause 2 needs the TAG, and a line with
#: no tag field has not been shown to come from the system.
_LINE_RE = re.compile(
    r"^\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3}\s+\d+\s+\d+\s+"
    r"([VDIWEFS])\s+([^:]{1,64}?)\s*:\s?(.*)$"
)

#: (kind, tags that own the marker, marker test, attribution test). ONE table, so
#: a kind cannot exist with no tag or no attribution rule.
_TAGS_CRASH = ("androidruntime",)
_TAGS_NATIVE = ("debug", "libc")
_TAGS_AM = ("activitymanager",)
