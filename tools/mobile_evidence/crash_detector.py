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
``tools/mobile/perception`` rather than being copied, for the reason stated in
``perception._guard_sentinel``: a copy of a guard phrase drifts silently. The
CHAT path, which does reach a model, wraps them: ``tools/mobile/render.crash_note``.
"""

from __future__ import annotations

import logging
import re

from tools.mobile.perception import GUARD_MARKERS, NEUTRALIZED

logger = logging.getLogger(__name__)

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


def _neutralize(value: object) -> str:
    """One device-sourced string, safe to STORE and to SHOW. Never raises."""
    try:
        text = value if isinstance(value, str) else str(value or "")
    except Exception:  # pragma: no cover - a __str__ that raises
        return ""
    text = _CONTROL_RE.sub("", text).replace("\r", " ").replace("\n", " ")
    for marker in GUARD_MARKERS:
        if marker and marker.lower() in text.lower():
            text = re.sub(re.escape(marker), NEUTRALIZED, text, flags=re.IGNORECASE)
    return _WS_RE.sub(" ", text).strip()


def _empty(reason: str, *, lines: int = 0, truncated: bool = False) -> dict:
    """The not-detected record. Always carries a reason: silence is not evidence."""
    return {
        "detected": False,
        "kind": "",
        "label": "",
        "marker": "",
        "excerpt": "",
        "package": "",
        "lines_scanned": int(lines),
        "truncated": bool(truncated),
        "reason": str(reason)[:200],
    }


def _package_re(package: str):
    """A WHOLE-package matcher. ``com.example.app`` must not match
    ``com.example.app.debug`` -- clause 4, and the reason this is a regex rather
    than an ``in``."""
    return re.compile(r"(?<![\w.])" + re.escape(package) + r"(?![\w.])")


def _kind_of(tag: str, message: str) -> str:
    """The kind this line's TAG and text prove, or "". Clause 2 lives here."""
    low = tag.strip().lower()
    body = message
    if low in _TAGS_CRASH and "FATAL EXCEPTION" in body:
        return KIND_APP_CRASH
    if low in _TAGS_NATIVE and ("Fatal signal" in body or "tombstone" in body):
        return KIND_NATIVE_ABORT
    if low in _TAGS_AM and body.lstrip().startswith("ANR in "):
        return KIND_ANR
    if low in _TAGS_AM and (
        ("has died" in body and body.lstrip().startswith("Process "))
        or body.lstrip().startswith("Force finishing activity ")
    ):
        return KIND_PROCESS_DEATH
    return ""


def _attributes(kind: str, message: str, matcher) -> bool:
    """Whether THIS line attributes *kind* to the package. Clause 3, same line."""
    if kind == KIND_APP_CRASH:
        return bool(message.lstrip().startswith("Process:")) and bool(
            matcher.search(message)
        )
    if kind == KIND_NATIVE_ABORT:
        return ">>>" in message and bool(matcher.search(message))
    return bool(matcher.search(message))


def scan(text: object, package: object) -> dict:
    """Judge one slice. ``{"detected", "kind", "label", "marker", "excerpt",
    "package", "lines_scanned", "truncated", "reason"}``. Never raises.

    ``detected`` is True only with a kind AND a marker line AND the package under
    test attributed to that marker.
    """
    try:
        pkg = str(package or "").strip()
        if not pkg:
            return _empty("no package under test was given, so nothing can be attributed")
        try:
            body = text if isinstance(text, str) else str(text or "")
        except Exception:  # pragma: no cover - a __str__ that raises
            return _empty("the slice could not be read as text")
        if not body.strip():
            return _empty("the slice is empty, so it proves nothing either way")
        raw = body.encode("utf-8", errors="replace")
        truncated = len(raw) > MAX_SCAN_BYTES
        if truncated:
            # THE TAIL, not the head. A crash ends the process.
            body = raw[-MAX_SCAN_BYTES:].decode("utf-8", errors="replace")
        lines = body.splitlines()
        matcher = _package_re(pkg)
        parsed = []
        for line in lines:
            hit = _LINE_RE.match(line)
            parsed.append(
                (hit.group(2), hit.group(3), line) if hit else ("", "", line)
            )
        seen_marker = ""
        for index, (tag, message, line) in enumerate(parsed):
            if not tag:
                continue
            kind = _kind_of(tag, message)
            if not kind:
                continue
            seen_marker = kind
            window = parsed[index : index + 1 + MAX_ATTRIBUTION_LINES]
            attributed = any(
                _attributes(kind, entry[1], matcher) for entry in window if entry[0]
            )
            if not attributed:
                continue
            excerpt = "\n".join(
                _neutralize(entry[2])
                for entry in parsed[index : index + MAX_EXCERPT_LINES]
            )[:MAX_EXCERPT_CHARS]
            return {
                "detected": True,
                "kind": kind,
                "label": KIND_LABELS.get(kind, "the app under test died"),
                "marker": _neutralize(line)[:MAX_MARKER_CHARS],
                "excerpt": excerpt,
                "package": pkg[:120],
                "lines_scanned": len(lines),
                "truncated": truncated,
                "reason": "",
            }
        if seen_marker:
            return _empty(
                "the "
                + seen_marker
                + " marker in this slice names another process, not "
                + pkg[:80]
                + ", so it is not this case's failure",
                lines=len(lines),
                truncated=truncated,
            )
        return _empty(
            "no fatal-event marker from the platform is in this slice",
            lines=len(lines),
            truncated=truncated,
        )
    except Exception:  # never-raise: a detector that raises is a detector that is off
        logger.exception("mobile_evidence.crash_detector.scan failed")
        return _empty("the slice could not be scanned")


def crash_of_case(case: object) -> dict:
    """The crash record on a CASE checkpoint, or ``{}``. Never raises.

    THE one place that knows where a crash lives -- ``case["evidence"]["crash"]``
    -- and that a record is only a crash when ``detected``. The report and the
    chat both call this instead of walking the record themselves: two walks are
    two derivations of the same answer, and mirrored conditions drift.
    """
    try:
        body = case if isinstance(case, dict) else {}
        evidence = body.get("evidence")
        evidence = evidence if isinstance(evidence, dict) else {}
        crash = evidence.get("crash")
        if isinstance(crash, dict) and crash.get("detected"):
            return crash
        return {}
    except Exception:  # pragma: no cover - defensive
        logger.exception("mobile_evidence.crash_detector.crash_of_case failed")
        return {}


def server_reason(crash: object, model_reason: object = "") -> str:
    """THE override reason. One producer, and it never impersonates the model.

    The server's own attribution is the FIRST clause, because
    ``render.verdict_line`` clips a reason to 160 characters -- a sentence that
    explains itself after the clip explains nothing. The model's own words are
    quoted, labelled and LAST, so the tester can see both judgements and whose
    each one is.
    """
    try:
        body = crash if isinstance(crash, dict) else {}
        label = str(body.get("label") or "the app under test died")[:120]
        marker = str(body.get("marker") or "")[:MAX_MARKER_CHARS]
        mine = str(model_reason or "").strip()[:200]
        text = (
            SERVER_PREFIX
            + " the app under test crashed during this case ("
            + label
            + "), so a pass cannot stand. This is the server's call, not your"
            " own judgement, and the run's own logcat is the proof."
        )
        if marker:
            text += " The line that proves it: " + marker
        if mine:
            text += ' The model reported: "' + mine + '"'
        return text
    except Exception:  # pragma: no cover - defensive
        logger.exception("mobile_evidence.crash_detector.server_reason failed")
        return SERVER_PREFIX + " the app under test crashed during this case."
