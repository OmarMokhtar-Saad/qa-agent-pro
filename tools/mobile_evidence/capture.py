"""The device side of app-log evidence: clear, slice, pull -- and scrub before disk.

Three calls, in the order a case runs them (plan D5):

* :func:`begin_case` -- ``logcat -c`` so the ring buffer starts with the app's own
  start-up, the device clock (for the offset the join needs, plan D6) and the app's
  pid (so the slice can be narrowed to it).
* :func:`slice_case` -- ``logcat -d`` at every checkpoint, filtered to the profile's
  tag and the pid, SCRUBBED line by line, then written under ``evidence/<tc_id>/``.
* :func:`pull_events` -- at run end, ``run-as <pkg> cat`` of every segment of the
  app's own event log, SCRUBBED record by record, concatenated into
  ``evidence/events.ndjson``. A release build refuses ``run-as``; the result then
  says ``events_source: "logcat-only"`` with the reason, and nothing raises.

**Both flags are read HERE, before any adb call** (plan D3): the lane's kill-switch
``QA_MOBILE_RUN_ENABLED`` and the operator-choice ``QA_MOBILE_APP_EVIDENCE``. A
guard on a caller is only as good as the list of callers, and this module is
importable from anywhere. A package with no profile makes NO adb call at all and
every function reports ``skipped`` with the reason -- the report then says "no app
log captured", never a blank.

**Redaction happens before the first byte reaches disk** (plan D4). The tester's
typed values for this call are armed into the value net for the duration of the
slice and forgotten in a ``finally``; the key/pair nets need no arming and run
always. ``run_store``'s own redaction is dict-key based and cannot see free text,
so this module is where a logcat line is made safe. The two secret tests that
``rglob`` every file under the cache root are the reason.

**``run-as`` is only ever given the run's own package argument** -- never a string
read from evidence, never a value from a profile field. Paths are built from the
profile's log-dir pattern and the segment names ``ls`` returned, each validated by
``adb`` before it is used.

This module knows no app: every vocabulary word comes from the profile.
"""

from __future__ import annotations

import json
import logging
import re
import time

from config.settings import settings
from tools.mobile import adb, run_store
from tools.mobile_evidence import profiles, scrub

logger = logging.getLogger(__name__)

FLAG_NAME = "QA_MOBILE_RUN_ENABLED"
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

EVENTS_FILE = "events.ndjson"

#: What the case carries when the device clock could not be read (plan D6).
CLOCK_NOT_READ = "device clock not read -- attribution by host clock, ±2 s"

_SEGMENT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,120}$")

#: Typed values per run, IN MEMORY ONLY. The lane types a credential in one call and
#: pulls the app's event log at run end, in another call; a value the app echoed into
#: that log can only be masked if the pull still knows it. Never written anywhere.
_TYPED_BY_RUN: dict[str, set[str]] = {}

#: The memory is BOUNDED, because "abandoned" is a state the lane computes from a
#: stale lease, not an event it could hook: a run whose chat was closed never
#: reaches ``pull_events`` and would otherwise keep its typed values in this
#: process until restart. Oldest run first out. A run evicted before its pull is
#: not armed from memory at pull time -- the key and prose nets still apply --
#: and docs/MOBILE_TESTING.md states the limit.
MAX_REMEMBERED_RUNS = 8


def remember_typed(run_id: str, values) -> int:
    """Keep this call's typed values for the run-end pull. Returns the set size."""
    key = str(run_id)
    # LAST-TOUCH order: a run that is still typing is moved to the back, so the
    # victim of the bound is the run nobody has driven for longest, never an
    # active one. The eviction is logged by run id -- a disarmed mask must not
    # be silent.
    bucket = _TYPED_BY_RUN.pop(key, None)
    if bucket is None:
        bucket = set()
        while len(_TYPED_BY_RUN) >= MAX_REMEMBERED_RUNS:
            evicted = next(iter(_TYPED_BY_RUN))
            _TYPED_BY_RUN.pop(evicted, None)
            logger.warning(
                "mobile_evidence.capture: typed-value memory full; run %s forgotten "
                "before its pull, so its typed values will not be masked from memory",
                evicted,
            )
    _TYPED_BY_RUN[key] = bucket
    for value in values or ():
        text = str(value or "").strip()
        if len(text) >= scrub.MIN_SENSITIVE_LEN:
            bucket.add(text)
    return len(bucket)


def forget_run(run_id: str) -> None:
    """Drop a run's remembered typed values.

    Called from ``pull_events``' ``finally``, so it runs on EVERY exit of the
    pull -- refusal, no profile, run-as refused, exception -- not only after a
    successful one. The pull is the run's end either way.
    """
    _TYPED_BY_RUN.pop(str(run_id), None)


def _refusal() -> str | None:
    """The flag that says no, or None. The kill-switch is checked first."""
    if not settings.qa_mobile_run_enabled:
        return (
            "Refusing to capture app evidence: the mobile lane needs `"
            + FLAG_NAME
            + "=true` in `.env`. No adb call was made and nothing was written."
        )
    if not bool(getattr(settings, "qa_mobile_app_evidence", True)):
        return (
            "Refusing to capture app evidence: `"
            + EVIDENCE_FLAG_NAME
            + "=false` in `.env` keeps the app's own log off this machine. No adb "
            "call was made and nothing was written."
        )
    return None


def _skipped(reason: str) -> dict:
    return {
        "error": None,
        "content": {
            "skipped": str(reason)[:200],
            "profile": None,
            "clock_offset_ms": None,
            "pid": None,
            "clock_note": None,
        },
    }


def _profile(package: object) -> profiles.Profile | None:
    try:
        return profiles.profile_for(package)
    except Exception:  # pragma: no cover - profile_for never raises
        logger.exception("mobile_evidence.capture: profile lookup failed")
        return None


def _profile_or_generic(package: object) -> profiles.Profile:
    """The package's own profile, or the generic one. Never None.

    The fallback is decided HERE rather than inside ``profile_for``, so "this
    package has no profile" stays an observable fact and the record can say
    which of the two was used.
    """
    return _profile(package) or profiles.generic_profile(package)


def _is_generic(profile: object) -> bool:
    return getattr(profile, "source", "") == profiles.GENERIC_PROFILE_NAME


def _profile_label(profile: profiles.Profile) -> str:
    return str(profile.name or profile.package or profile.source)[:80]


async def begin_case(serial: str, package: str, run_id: str, tc_id: str) -> dict:
    """Clear the ring buffer and read the device clock and the app's pid.

    ``{"error", "content": {"skipped", "profile", "clock_offset_ms", "pid",
    "clock_note"}}``. ``clock_offset_ms`` is device minus host, in ms; ``None``
    when the device clock could not be read, in which case ``clock_note`` carries
    :data:`CLOCK_NOT_READ` -- never a silent zero. Never raises.
    """
    try:
        refusal = _refusal()
        if refusal:
            return {"error": refusal, "content": None}
        # No profile is no longer a reason to capture nothing: the app still has
        # a pid, and its logcat filtered to that pid is its own output.
        profile = _profile_or_generic(package)
        cleared = await adb.logcat_clear(serial)
        if cleared.get("error"):
            logger.info(
                "mobile_evidence.capture: logcat -c failed for %s: %s",
                tc_id,
                cleared["error"],
            )
        host_ms = int(time.time() * 1000)
        device_ms = await adb.device_epoch_ms(serial)
        offset = int(device_ms) - host_ms if isinstance(device_ms, int) else None
        pid = await adb.pidof(serial, package)
        return {
            "error": None,
            "content": {
                "skipped": None,
                "profile": _profile_label(profile),
                "clock_offset_ms": offset,
                "pid": pid,
                "clock_note": None if offset is not None else CLOCK_NOT_READ,
            },
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile_evidence.capture.begin_case failed")
        return {"error": str(exc), "content": None}


def _scrub_lines(text: str, tester_inputs: object) -> tuple[str, int]:
    """Every line through the nets, with the tester's values armed for the duration."""
    values = (
        list((tester_inputs or {}).values()) if isinstance(tester_inputs, dict) else []
    )
    try:
        scrub.arm(values)
        lines = [scrub.scrub_text(line) for line in str(text or "").splitlines()]
    finally:
        scrub.forget_sensitive()
    return ("\n".join(lines) + "\n") if lines else "", len(lines)


async def slice_case(
    serial: str,
    package: str,
    run_id: str,
    tc_id: str,
    index: int,
    *,
    begin: dict | None,
    tester_inputs: dict | None = None,
) -> dict:
    """``logcat -d`` for this case, scrubbed, written as ``evidence/<tc_id>/logcat-<index>.txt``.

    ``{"error", "content": {"skipped", "path", "lines", "truncated"}}``. Never raises.
    """
    try:
        refusal = _refusal()
        if refusal:
            return {"error": refusal, "content": None}
        began = begin if isinstance(begin, dict) else {}
        if began.get("skipped"):
            return {
                "error": None,
                "content": {
                    "skipped": str(began["skipped"])[:200],
                    "path": None,
                    "lines": 0,
                    "truncated": False,
                },
            }
        profile = _profile_or_generic(package)
        pid = began.get("pid")
        # RE-READ THE PID HERE, and this is the fix rather than a tidy-up.
        # `case_runner` calls force_stop -> begin_case -> launch, so `pidof` at
        # begin time asks about an app that is NOT RUNNING and answers nothing.
        # The pid was therefore always None on the real path, and the refusal
        # below -- added to stop a device-wide dump -- skipped the slice for
        # every unprofiled app instead. Its test was green because it injected
        # a pid that ordering cannot produce.
        #
        # By slice time the app has been launched and driven, so this is the
        # one moment the question has an answer.
        if not (isinstance(pid, int) and pid > 0):
            pid = await adb.pidof(serial, package)
        # THE GENERIC PROFILE HAS NO TAG, so the pid is the ONLY thing
        # narrowing the dump. Without it `logcat -d` carries neither `--pid`
        # nor `-s` and a megabyte of every app on the device is written into
        # this run's evidence and shown in its report -- confirmed by a review
        # that ran it. A real profile still has its tag, so this bound is on
        # the generic path alone.
        if _is_generic(profile) and not (isinstance(pid, int) and pid > 0):
            return {
                "error": None,
                "content": {
                    "skipped": (
                        "the app was not running when the slice was taken, so "
                        "its log could not be told apart from every other "
                        "app's; nothing was captured"
                    ),
                    "path": None,
                    "lines": 0,
                    "truncated": False,
                },
            }
        dumped = await adb.logcat_dump(
            serial,
            tag=str(profile.logcat_tag or ""),
            pid=int(pid) if isinstance(pid, int) and pid > 0 else None,
            max_bytes=(
                MAX_GENERIC_SLICE_BYTES if _is_generic(profile) else MAX_SLICE_BYTES
            ),
        )
        if dumped.get("error"):
            return dumped
        body = dumped.get("content") or {}
        text, count = _scrub_lines(str(body.get("text") or ""), tester_inputs)
        name = "logcat-" + str(max(0, int(index or 0))) + ".txt"
        written = run_store.write_evidence_text(run_id, tc_id, name, text)
        if written.get("error"):
            return written
        return {
            "error": None,
            "content": {
                "skipped": None,
                "path": str((written.get("content") or {}).get("path") or ""),
                "lines": count,
                "truncated": bool(body.get("truncated")),
            },
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile_evidence.capture.slice_case failed")
        return {"error": str(exc), "content": None}


def _logcat_only(reason: str) -> dict:
    return {
        "error": None,
        "content": {
            "events_source": "logcat-only",
            "path": None,
            "reason": str(reason)[:300],
            "segments": 0,
            "lines": 0,
            "truncated": False,
        },
    }


def _scrub_record(line: str) -> str:
    """One ndjson line: parsed and scrubbed as a structure when it is JSON, as text otherwise."""
    stripped = line.strip()
    if not stripped:
        return ""
    try:
        record = json.loads(stripped)
    except ValueError:
        return scrub.scrub_text(stripped)
    if not isinstance(record, (dict, list)):
        return scrub.scrub_text(stripped)
    return json.dumps(scrub.scrub_json(record), ensure_ascii=False, sort_keys=True)


async def pull_events(
    serial: str, package: str, run_id: str, *, tester_inputs: dict | None = None
) -> dict:
    """Pull the app's own event log segments into ``evidence/events.ndjson``.

    The typed-value net is armed with *tester_inputs* plus every value
    :func:`remember_typed` saw for this run, for the duration of the record loop
    (plan D4) -- the app echoes what was typed into its own log.

    ``{"error", "content": {"events_source": "ndjson"|"logcat-only", "path",
    "reason", "segments", "lines", "truncated"}}``. A ``run-as`` refusal (release
    build, unknown package) is the logcat-only branch WITH its reason. Never raises.
    """
    # The run's typed values are RELEASED ON EVERY EXIT, not only after a
    # successful pull: a release build refuses run-as and an unprofiled
    # package never gets this far, and both are routine -- a typed credential
    # must not outlive the run in process memory on either path.
    try:
        refusal = _refusal()
        if refusal:
            return {"error": refusal, "content": None}
        profile = _profile_or_generic(package)
        log_dir = profile.log_dir()
        if not log_dir:
            # The generic profile always lands here: it names no on-device log,
            # so there is no event stream to pull and the slices are the whole
            # of the evidence. Said in the reason rather than left as a blank.
            if _is_generic(profile):
                return _logcat_only(
                    "no profile for "
                    + str(package)[:80]
                    + "; a "
                    + profiles.GENERIC_PROFILE_NAME
                    + " captured the app's own logcat by pid instead, and there "
                    "is no on-device event log to pull"
                )
            return _logcat_only("the profile names no on-device log directory")
        try:
            segment_re = re.compile(str(profile.segment_name_regex or ""))
        except re.error as exc:
            return _logcat_only(
                "segment name pattern does not compile: " + str(exc)[:120]
            )
        if not profile.segment_name_regex:
            return _logcat_only("the profile names no segment file pattern")
        listed = await adb.run_as_ls(serial, package, log_dir)
        if listed.get("error"):
            return _logcat_only("run-as refused: " + str(listed["error"])[:200])
        names = sorted(
            name
            for name in (listed.get("content") or [])
            if _SEGMENT_NAME_RE.match(str(name)) and segment_re.match(str(name))
        )
        if not names:
            return _logcat_only("the app's log directory holds no event segment")
        remaining = MAX_EVENTS_BYTES
        parts: list[str] = []
        lines = 0
        truncated = False
        typed = set(_TYPED_BY_RUN.get(str(run_id), set()))
        typed.update(str(v) for v in (tester_inputs or {}).values())
        scrub.arm(typed)
        try:
            for name in names:
                if remaining <= 0:
                    truncated = True
                    break
                fetched = await adb.run_as_cat(
                    serial,
                    package,
                    log_dir.rstrip("/") + "/" + name,
                    max_bytes=remaining,
                )
                if fetched.get("error"):
                    return _logcat_only(
                        "run-as refused: " + str(fetched["error"])[:200]
                    )
                body = fetched.get("content") or {}
                data = body.get("data")
                text = (
                    data.decode("utf-8", errors="replace")
                    if isinstance(data, bytes)
                    else str(data or "")
                )
                truncated = truncated or bool(body.get("truncated"))
                remaining -= len(text.encode("utf-8", errors="replace"))
                for line in text.splitlines():
                    cleaned = _scrub_record(line)
                    if cleaned:
                        parts.append(cleaned)
                        lines += 1
        finally:
            scrub.forget_sensitive()
        written = run_store.write_evidence_text(
            run_id, None, EVENTS_FILE, ("\n".join(parts) + "\n") if parts else ""
        )
        if written.get("error"):
            return written
        return {
            "error": None,
            "content": {
                "events_source": "ndjson",
                "path": str((written.get("content") or {}).get("path") or ""),
                "reason": ("the " + str(MAX_EVENTS_BYTES) + " byte cap was reached")
                if truncated
                else None,
                "segments": len(names),
                "lines": lines,
                "truncated": truncated,
            },
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile_evidence.capture.pull_events failed")
        return {"error": str(exc), "content": None}
    finally:
        forget_run(run_id)
