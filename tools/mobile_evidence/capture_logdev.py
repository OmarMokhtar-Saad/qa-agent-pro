"""Every adb call of the app-log lane: clock, pid, logcat dump and event segments.

This is THE ONE ``adb`` HOME of the lane (begin, slice and pull all reach the
device through here), so a test that fakes the device patches ``adb`` in this
module. ``run-as`` is only ever given the run's own package argument -- never a
string read from evidence, never a value from a profile field. Paths are built
from the profile's log-dir pattern and the segment names ``ls`` returned, each
validated by ``adb`` before it is used.
"""

from __future__ import annotations

import logging
import re
import time
from typing import NamedTuple

from tools.mobile import adb
from tools.mobile.adb_logcat import LogQuery
from tools.mobile.adb_runas import RunAsRead
from tools.mobile_evidence import capture_consts as _consts
from tools.mobile_evidence import profiles
from tools.mobile_evidence.capture_profile import _is_generic
from tools.mobile_evidence.capture_records import _logcat_only
from tools.mobile_evidence.capture_scrub import _scrub_record

logger = logging.getLogger("tools.mobile_evidence.capture")


class _LogTarget(NamedTuple):
    serial: str
    package: str
    log_dir: str


async def _clear_logcat(serial: str, tc_id: str) -> None:
    cleared = await adb.logcat_clear(serial)
    if cleared.get("error"):
        logger.info(
            "mobile_evidence.capture: logcat -c failed for %s: %s",
            tc_id,
            cleared["error"],
        )


async def _clock_offset(serial: str) -> int | None:
    host_ms = int(time.time() * 1000)
    device_ms = await adb.device_epoch_ms(serial)
    return int(device_ms) - host_ms if isinstance(device_ms, int) else None


async def _app_pid(serial: str, package: str):
    return await adb.pidof(serial, package)


async def _slice_pid(serial: str, package: str, began: dict) -> int | None:
    """The app's pid for the slice: the begin-time one when valid, else asked again now.

    RE-READ THE PID HERE, and this is the fix rather than a tidy-up.
    `case_runner` calls force_stop -> begin_case -> launch, so `pidof` at begin
    time asks about an app that is NOT RUNNING and answers nothing. The pid was
    therefore always None on the real path, and the refusal in ``slice_case`` --
    added to stop a device-wide dump -- skipped the slice for every unprofiled
    app instead. By slice time the app has been launched and driven, so this is
    the one moment the question has an answer.
    """
    pid = began.get("pid")
    if not (isinstance(pid, int) and pid > 0):
        pid = await adb.pidof(serial, package)
    return int(pid) if isinstance(pid, int) and pid > 0 else None


async def _dump_slice(serial: str, profile: profiles.Profile, pid: int | None) -> dict:
    return await adb.logcat_dump(
        serial,
        LogQuery(
            tag=str(profile.logcat_tag or ""),
            pid=pid,
            max_bytes=_consts.MAX_GENERIC_SLICE_BYTES
            if _is_generic(profile)
            else _consts.MAX_SLICE_BYTES,
        ),
    )


def _compile_segment_re(profile: profiles.Profile) -> re.Pattern[str] | dict:
    try:
        return re.compile(str(profile.segment_name_regex or ""))
    except re.error as exc:
        return _logcat_only("segment name pattern does not compile: " + str(exc)[:120])


async def _segment_names(
    target: _LogTarget, profile: profiles.Profile
) -> tuple[list[str], dict | None]:
    """The event segment names, or a logcat-only record saying why there are none."""
    segment_re = _compile_segment_re(profile)
    if isinstance(segment_re, dict):
        return [], segment_re
    if not profile.segment_name_regex:
        return [], _logcat_only("the profile names no segment file pattern")
    listed = await adb.run_as_ls(target.serial, target.package, target.log_dir)
    if listed.get("error"):
        return [], _logcat_only("run-as refused: " + str(listed["error"])[:200])
    names = sorted(
        name
        for name in (listed.get("content") or [])
        if _consts._SEGMENT_NAME_RE.match(str(name)) and segment_re.match(str(name))
    )
    if not names:
        return [], _logcat_only("the app's log directory holds no event segment")
    return names, None


def _segment_text(data: object) -> str:
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return str(data or "")


async def _read_one(
    target: _LogTarget, name: str, remaining: int
) -> tuple[dict | None, str, bool]:
    """Fetch one segment: (logcat-only record on refusal, its text, truncated)."""
    fetched = await adb.run_as_cat(
        target.serial,
        RunAsRead(
            target.package,
            target.log_dir.rstrip("/") + "/" + name,
            max_bytes=remaining,
        ),
    )
    if fetched.get("error"):
        return _logcat_only("run-as refused: " + str(fetched["error"])[:200]), "", False
    body = fetched.get("content") or {}
    return None, _segment_text(body.get("data")), bool(body.get("truncated"))


async def _read_segments(
    target: _LogTarget, names: list[str]
) -> tuple[dict | None, list[str], bool]:
    """Fetch and scrub each segment: (logcat-only record on refusal, records, truncated)."""
    remaining = _consts.MAX_EVENTS_BYTES
    parts: list[str] = []
    truncated = False
    for name in names:
        if remaining <= 0:
            truncated = True
            break
        refused, text, cut = await _read_one(target, name, remaining)
        if refused is not None:
            return refused, [], False
        truncated = truncated or cut
        remaining -= len(text.encode("utf-8", errors="replace"))
        parts.extend(c for c in map(_scrub_record, text.splitlines()) if c)
    return None, parts, truncated
