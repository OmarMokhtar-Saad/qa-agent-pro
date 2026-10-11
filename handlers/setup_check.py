"""Moved from handlers (refactor P2): setup_check."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple

from handlers import _common, _reply, export, host_check, mobile_gate, prep_status
from tools.jira_mcp import connect_hint_line

logger = logging.getLogger("tools.mcp_handlers")

# Baked by scripts/build_dist.py in the public distribution ("owner/repo").
# Empty in the private checkout, which disables the on-demand update path.
_DIST_UPDATE_REPO = "OmarMokhtar-Saad/qa-agent-pro"

# FROZEN-SCHEMA POLICY: editors cache tool definitions for the whole session
# and ignore list_changed, so a signature change is invisible until the editor
# restarts. Keep tool names/params stable; when a release DOES change one,
# bump this to that release version — qa-doctor then tells users whose
# session predates it that a one-time editor restart is needed.
_TOOL_SCHEMAS_CHANGED_IN = "1.14.0"


# ops-8: a reload REPLACES this process, so the response that schedules one can
# never say whether it worked -- that response is already flushed by the time
# the exit happens. _schedule_reload leaves this breadcrumb behind instead, and
# the NEXT handle_setup_check (a different process, after the launcher respawn)
# reads it back and reports the outcome. It lives beside the launcher's existing
# backups/session-state.json rather than in a new state directory.
_RELOAD_MARKER_NAME = "reload-marker.json"

# How long a marker stays actionable. A reload is a 2s exit plus a respawn plus
# an MCP handshake -- seconds, not minutes -- but a boot that reinstalls
# dependencies can take a couple of minutes, so allow generous slack. Past this
# the marker is assumed orphaned (the process died, or the editor was closed
# before anyone re-checked) and is discarded silently: an ancient breadcrumb
# must never warn "reload did not take effect" on every setup check forever.
_RELOAD_MARKER_TTL_S = 300.0


def _reload_marker_path() -> Path:
    """Where the reload breadcrumb lives. _INSTALL_DIR is imported lazily like
    every other use in this module, so tests can repoint the install dir."""
    from tools.updater import _INSTALL_DIR

    return Path(_INSTALL_DIR) / "backups" / _RELOAD_MARKER_NAME


def _write_reload_marker(reason: str) -> None:
    """Record what this process was running, moments before it exits to reload.

    ``version`` is the RUNNING version (_BOOT_VERSION), never the on-disk one:
    after an update the on-disk VERSION is ALREADY the new value, so recording
    it would compare equal in the successor and read as a failed reload.

    Never raises -- an unwritable install dir only costs the confirmation line
    on the next call, and must never turn a working reload into a failed tool
    call."""
    try:
        path = _reload_marker_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "version": _common._BOOT_VERSION,
                    "reason": reason,
                    "pid": os.getpid(),
                    "at": time.time(),
                }
            ),
            encoding="utf-8",
        )
    except Exception:
        logger.debug("could not write the reload marker", exc_info=True)


def _consume_reload_marker() -> dict:
    """Read the reload breadcrumb and DELETE it, so an outcome is reported once.

    Returns ``{}`` when there is nothing to report: no marker, an unreadable or
    corrupt one, or one outside _RELOAD_MARKER_TTL_S (including a marker dated
    in the FUTURE, which means a clock change, not a reload). Never raises -- it
    feeds a report whose contract is "read-only and never raises"."""
    try:
        path = _reload_marker_path()
        raw = path.read_text(encoding="utf-8")
    except Exception:
        return {}
    try:
        path.unlink()
    except Exception:
        # A marker that cannot be removed would repeat its verdict; the TTL
        # below bounds how long that can go on.
        logger.debug("could not remove the reload marker", exc_info=True)
    return _parse_reload_marker(raw)


def _peek_reload_marker() -> dict:
    """Read the reload breadcrumb WITHOUT deleting it. build_server() uses this
    for its startup notice so qa-doctor's _consume_reload_marker() still finds
    the marker and reports the reload outcome. Never raises."""
    try:
        raw = _reload_marker_path().read_text(encoding="utf-8")
    except Exception:
        return {}
    return _parse_reload_marker(raw)


def _parse_reload_marker(raw: str) -> dict:
    """The marker dict, or {} when it is corrupt or outside
    _RELOAD_MARKER_TTL_S (a future timestamp is a clock change, not a
    reload). Never raises."""
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return {}
        age = time.time() - float(data.get("at") or 0)
    except Exception:
        logger.debug("unreadable reload marker ignored", exc_info=True)
        return {}
    if age < 0 or age > _RELOAD_MARKER_TTL_S:
        return {}
    return data


def _reload_outcome(marker: dict) -> tuple[str, str]:
    """Turn a fresh marker into ``(report_note, action_item)``; both empty when
    there is nothing to say.

    A reload took effect when a DIFFERENT process is answering now, so the pid
    is the primary signal. The version is secondary, and only for the reasons
    that are SUPPOSED to change it: an "update"/"code" reload that came back on
    the same version means the launcher respawned the same tree. A "config"
    reload is expected to keep its version, so judging it on the version would
    report every successful .env reload as a failure."""
    if not marker:
        return "", ""
    now_v = _common._BOOT_VERSION or ""
    prev_v = str(marker.get("version") or "")
    same_process = marker.get("pid") == os.getpid()
    stuck = (
        str(marker.get("reason") or "") in ("update", "code")
        and bool(prev_v)
        and prev_v == now_v
    )
    if same_process or stuck:
        shown = f"v{now_v}" if now_v else "the version it started with"
        return (
            f"⚠️ **Reload did not take effect** — still running {shown}. If this "
            "persists, restart the editor (quit and reopen it) or reinstall the "
            "agent.",
            f"The last reload did not take effect — still running {shown}. Quit "
            "and reopen the editor; if it still says this, reinstall the agent.",
        )
    shift = (
        f"v{prev_v} → v{now_v}"
        if prev_v and now_v and prev_v != now_v
        else f"v{now_v or 'unknown'}"
    )
    return f"✅ **Reloaded successfully** ({shift})", ""


def _reloading_message(headline: str) -> str:
    """The ONLY thing a setup check that just scheduled a reload may say.

    Everything the full report renders -- backend, Jira, feature gates -- comes
    from THIS process's in-memory config, which is seconds from being thrown
    away. Splicing a note into that report (what this used to do) still showed
    the tester a complete-looking "✅ Ready" verdict over values that could
    already be wrong, so say only what is certainly true and stop."""
    return (
        "## Setup check\n\n"
        f"{headline} This takes about 10 seconds — run `qa-doctor` again "
        "to see the current status, including whether the reload worked.\n\n"
        "_Environment, backend, Jira and feature-gate details are deliberately "
        "not shown here: this process is being replaced, so anything it "
        "reported could already be out of date._"
    )


def _schedule_reload(reason: str = "") -> None:
    """Exit the server process shortly after the current response flushes.

    Distribution installs only, right after an on-demand update: the
    supervising launcher (start.sh) respawns the server on the NEW code and
    replays the MCP handshake, so the editor session never notices.

    *reason* is "update", "code" or "config" and is recorded in the reload
    marker so the successor can judge the outcome: an update reload must come
    back on a new version, while a config reload is expected to keep the same
    one. The marker is written HERE, before the thread starts -- so it is always
    on disk before the exit, and EVERY caller gets the verification for free."""
    _write_reload_marker(reason)

    def _later() -> None:
        time.sleep(2)
        os._exit(86)

    threading.Thread(target=_later, daemon=True).start()


def _drift_advisory() -> str:
    """qa-doctor's drift line for an install that cannot reload itself.

    2026-08-31 (qa-doctor/qa-restart use-case sweep): the two drift checks
    below were reachable ONLY from inside `if _test_cases_only() and
    _DIST_UPDATE_REPO:`. `_DIST_UPDATE_REPO` is "" in every private checkout
    and in any dist build without an update repo, so on those installs
    qa-doctor rendered settings from the `.env` it BOOTED with and said
    nothing -- the exact staleness those checks exist to catch, silent on the
    installs where a developer edits `.env` most often.

    It is an ADVISORY here, not a reload, and that difference is the whole
    reason this is a separate function: `_schedule_reload` hard-exits the
    process, which is safe only because a supervising launcher respawns it and
    replays the handshake. An unsupervised server that exited would be marked
    `failed` by the client and respawned by nobody, so turning a stale-config
    notice into a dead session is strictly worse than reporting it. Recommended
    rather than blocking: the server still answers every tool call correctly,
    it is just answering on older configuration.

    Returns "" when nothing drifted, which is the normal case."""
    try:
        code = export._code_changed_since_start()
        env = export._env_changed_since_start()
    except Exception:  # pragma: no cover - an advisory must never break qa-doctor
        logger.debug("drift advisory failed", exc_info=True)
        return ""
    if code:
        return (
            "**This server is running older code than is on disk.** Another "
            "process updated this install after this one started, so it is "
            "serving stale modules -- and anything reported below describes "
            "the OLD code. Restart the MCP server (`python3 "
            ".claude/operations/scripts/qa-restart.py`, or reconnect it in "
            "your client) to pick the new code up."
        )
    if env:
        return (
            "**`.env` was edited after this server started.** The settings "
            "reported above are the ones this process booted with, NOT what "
            "the file says now. Restart the MCP server (`python3 "
            ".claude/operations/scripts/qa-restart.py`, or reconnect it in "
            "your client) to apply them."
        )
    return ""


async def _mobile_ime_rows(serials: list) -> list:
    """Per-device QA-keyboard lines: installed, selected, and does it answer.

    THE QUESTION THE REST OF THE SECTION DOES NOT ASK. Every other row here is
    about whether the lane can DRIVE a device; these are about whether it can
    TYPE on one, which has a different answer -- a device can be attached, free
    and running the tester's own keyboard, in which case every row above is a
    tick and no script that types will do anything at all.
    """
    out: list = []
    try:
        from tools.mobile import ime, ime_session
    except Exception:
        logger.debug("qa-doctor keyboard rows skipped: modules absent", exc_info=True)
        return out
    for serial in serials:
        where = str(serial or "?")
        found = await _mobile_ime_state(ime_session, where)
        rows = _mobile_ime_static_rows(ime, where, found)
        out.extend(rows or await _mobile_ime_probe_rows(ime, where))
    return out


async def _mobile_ime_state(ime_session, where: str) -> dict:
    """The keyboard state of *where*, or an error dict when the probe fails."""
    try:
        return await asyncio.wait_for(
            ime_session.state(where), timeout=mobile_gate._MOBILE_IME_STATE_S
        )
    except Exception as exc:
        # A timeout's str() is empty, which printed "did not finish ()".
        why = (
            "within " + str(int(mobile_gate._MOBILE_IME_STATE_S)) + "s"
            if isinstance(exc, asyncio.TimeoutError)
            else "(" + str(exc)[:80] + ")"
        )
        return {
            "error": "the keyboard probe did not finish " + why,
            "content": None,
        }


def _mobile_ime_static_rows(ime, where: str, found: dict) -> list:
    """The one line for a failed probe or a keyboard that is missing,
    outdated or not selected; [] when the keyboard needs a live probe."""
    out: list = []
    if found.get("error") or found.get("content") is None:
        # NOT "the keyboard is missing". A probe that failed says nothing
        # about the device, and this file has shipped that confusion before.
        out.append(
            "- \u26a0\ufe0f Could not check the QA keyboard on "
            + where
            + ": "
            + str(found.get("error") or "the probe failed")[:160]
        )
        return out
    body = found.get("content") or {}
    if not body.get("installed"):
        out.append(
            "- \u2b1c The QA keyboard is not installed on "
            + where
            + " yet, so typing is unavailable there until a run installs "
            "it; the run hands your own keyboard back when it finishes"
        )
        return out
    if body.get("pinned") is False:
        # Only False: None is "could not tell" (no sha256sum, say), which a
        # run also accepts, so it is no reason to warn.
        out.append(
            "- ⬜ The QA keyboard on "
            + where
            + " is not the build this release ships, so the next run that "
            "types replaces it first; nothing for you to do"
        )
        return out
    if not body.get("selected"):
        out.append(
            "- \u2b1c The QA keyboard is installed on "
            + where
            + " but `"
            + (ime.display_id(body.get("current")) or "another keyboard")
            + "` is the active one, so typing is unavailable until a run "
            "selects it; the run restores yours afterwards"
        )
        return out
    return out


async def _mobile_ime_probe_rows(ime, where: str) -> list:
    """The one line for a selected keyboard, from a live test message."""
    out: list = []
    try:
        answered = await asyncio.wait_for(
            ime.probe(where), timeout=_reply._MOBILE_PROBE_S
        )
    except Exception as exc:
        answered = {"error": str(exc)[:80], "content": None}
    if answered.get("error") or not (answered.get("content") or {}).get("ok"):
        # SELECTED IS NOT WORKING, and that gap is the whole reason this
        # change exists: ADBKeyBoard is selected on API 35 and its receiver
        # never registers, so a run types into silence and reports success.
        # A keyboard that does not answer is reported as broken rather than
        # assumed good because it is selected.
        out.append(
            "- \u26a0\ufe0f The QA keyboard is active on "
            + where
            + " but did not answer a test message, so typing may silently "
            "do nothing: "
            + str(answered.get("error") or "no reply from the input method")[:160]
        )
        return out
    out.append("- \u2705 The QA keyboard is active on " + where + " and answering")
    return out


async def _mobile_off_attached_note() -> list:
    """What the lane-OFF disclosure adds when a device is ALREADY visible.

    Empty unless an adb server is already running, so an install with the lane
    off and no adb server gets exactly the text it gets today and this function
    makes no subprocess call whatsoever.
    """
    if not mobile_gate._adb_server_already_running():
        return []
    try:
        from tools.mobile import adb as mobile_adb

        attached = await asyncio.wait_for(
            mobile_adb.devices(), timeout=_reply._MOBILE_PROBE_S
        )
    except Exception:
        logger.debug("qa-doctor off-lane device note skipped", exc_info=True)
        return []
    if attached.get("error") or attached.get("content") is None:
        return []
    serials = [str(s) for s in (attached.get("content") or [])]
    if not serials:
        return []
    return [
        "- \u2139\ufe0f "
        + str(len(serials))
        + " Android device(s) are attached right now ("
        + ", ".join(serials[:4])
        + "). Nothing here is driving them while this lane is off, and nothing "
        "was started to find them.",
    ]


async def _mobile_avd_rows() -> list:
    """The AVD lines of the mobile doctor section: configured emulators, heavy
    AVDs and host load."""
    from tools.mobile import avd_guard, sdk_locator
    from tools.mobile import emulator as doctor_emulator
    from tools.mobile import render as doctor_render

    lines: list = []
    # The SAME producer the run's device stage asks. `list_avds` runs its
    # subprocess on a worker thread, so this wait really is the bound.
    try:
        avds = await asyncio.wait_for(
            doctor_emulator.list_avds(), timeout=_reply._MOBILE_PROBE_S
        )
    except Exception:  # a slow probe is not a missing AVD
        avds = {"error": "the AVD probe did not finish", "content": None}
    names = list(avds.get("content") or [])
    if avds.get("error"):
        lines.append("- ⬜ Emulators (AVDs) not checked: " + str(avds["error"])[:160])
    elif names:
        lines.append(
            "- ✅ "
            + str(len(names))
            + " emulator(s) configured: "
            + ", ".join("`" + str(n)[:40] + "`" for n in names[:5])
        )
    else:
        lines.append(
            "- ⬜ No AVD → create one in Android Studio's Device Manager ("
            + doctor_render.AVD_GUIDE_URL
            + "); `qa_mobile_test` answers with the same setup guide"
        )
    # Fix round 2, item 7: a heavy AVD is read from its config.ini and
    # the host load from the OS, so this needs no booted device.
    for name in names[:5]:
        shown = sdk_locator.avd_display(name).get("content") or {}
        heavy = avd_guard.assess(
            shown.get("width", 0), shown.get("height", 0), shown.get("density")
        )
        if heavy:
            lines.append("- ⚠️ `" + str(name)[:40] + "`: " + heavy)
    loaded = avd_guard.assess(load_per_core=avd_guard.host_load())
    if loaded:
        lines.append("- ⚠️ " + loaded)
    return lines


async def _mobile_attached_devices() -> dict:
    """Ask adb which devices are attached; a slow probe is not an absent device.

    THE DEVICE LOCKS, ONE PER DEVICE, ASKED BY NAME: the design's honest bound
    (plan §5.6) is that a holder whose heartbeat writer never started is NOT
    force-released, and the cost of that choice is disclosed instead."""
    from tools.mobile import adb as mobile_adb

    try:
        return await asyncio.wait_for(
            mobile_adb.devices(), timeout=_reply._MOBILE_PROBE_S
        )
    except Exception as exc:  # a slow probe is not an absent device
        return {
            "error": "the device probe did not finish (" + str(exc)[:80] + ")",
            "content": None,
        }


def _mobile_lock_row_line(row: dict) -> str:
    """One doctor line for one device's lock row."""
    where = str(row.get("serial") or "?")
    if row.get("error"):
        return (
            "- ⚠️ Could not check whether a run holds "
            + where
            + ": "
            + str(row.get("error"))[:160]
        )
    if row.get("held_too_long"):
        return (
            "- ⚠️ "
            + where
            + " has been held by pid "
            + str(row.get("pid") or "?")
            + " (run `"
            + str(row.get("owner") or "?")
            + "`) for "
            + str(int(row.get("age") or 0))
            + "s. Nothing here will break that hold — a lock taken "
            "from a live holder means two runs on one device. If "
            "that process is wedged, quit the editor holding it."
        )
    if row.get("held"):
        return (
            "- ✅ "
            + where
            + " is in use by run `"
            + str(row.get("owner") or "?")
            + "` — one run drives a device at a time, and the other "
            "devices below are unaffected"
        )
    return "- ✅ " + where + " is free"


def _mobile_lock_rows(attached: dict) -> list:
    """The device-lock lines, ONE PER DEVICE.

    IT MUST NAME THE DEVICE: a probe with no argument reads a lock nobody takes
    and reports "free" while a device is being driven."""
    from tools.mobile import locks as mobile_locks

    if attached.get("error") or attached.get("content") is None:
        # FAILS CLOSED: "no run holds a device" would be a guess, and the wrong one.
        return [
            "- ⚠️ Could not check which devices are attached, so nothing "
            "here says whether a run holds one: "
            + str(attached.get("error") or "the probe failed")[:160]
        ]
    serials = [str(s) for s in (attached.get("content") or [])]
    lines = [] if serials else ["- ⬜ No device is attached, so no run holds one"]
    lines += [_mobile_lock_row_line(r) for r in mobile_locks.device_holders(serials)]
    return lines


async def _mobile_doctor_section() -> list:
    """The qa-doctor lines for the mobile lane. Never raises, never blocks.

    Bounded on purpose: qa-doctor is the FIRST thing a tester runs on a new
    machine, and a device probe that hangs turns the one diagnostic tool into
    the symptom. The probe is wrapped in ``asyncio.wait_for``; a timeout is
    reported as "not checked", never as absent hardware -- telling a tester
    their emulator is missing because our probe was slow is exactly the class of
    untrue tester-facing text this file has been corrected for before.
    """
    if not _common._mobile_lane_enabled():
        return []
    lines = [
        "### Mobile emulator lane",
        "- Device actions go through `qa_mobile_test` (goal=... for ad-hoc"
        " steps), not raw adb: it owns the safety guard, keyboard handling and"
        " evidence.",
    ]
    lines += await _mobile_sdk_ime_lines()
    lines += await _mobile_emulator_lines()
    return lines


async def _mobile_sdk_ime_lines() -> list:
    """SDK, AVD, device-lock and keyboard lines; one note when they cannot be read."""
    lines: list = []
    try:
        from tools.mobile import render as doctor_render
        from tools.mobile import sdk_locator

        located = (sdk_locator.locate_sdk() or {}).get("content") or {}
        root = str(located.get("sdk_root") or "")
        lines.append(
            "- ✅ Android SDK found at `"
            + root
            + "` ("
            + str(located.get("source") or "?")
            + ")"
            if root
            else "- ⬜ Android SDK not found → `qa_mobile_test` answers with a "
            "setup guide (install Android Studio: "
            + doctor_render.ANDROID_STUDIO_URL
            + "). This server downloads no SDK."
        )
        if root:
            lines += await _mobile_avd_rows()
        await _mobile_lock_ime_lines(lines)
    except Exception:
        logger.debug("qa-doctor mobile sdk/ime section skipped", exc_info=True)
        lines.append("- ⬜ Mobile support modules could not be inspected")
    return lines


async def _mobile_lock_ime_lines(lines: list) -> None:
    """Append the per-device lock rows and the QA keyboard rows to *lines*."""
    from tools.mobile import ime

    # THE DEVICE LOCKS, ONE PER DEVICE, ASKED BY NAME. The design's honest
    # bound (plan §5.6) is that a holder whose heartbeat writer never
    # started is NOT force-released -- breaking a live holder's lock is the
    # two-holder defect the whole mechanism deletes -- and that the cost of
    # that choice is disclosed instead. This section IS that disclosure.
    #
    # IT MUST NAME THE DEVICE. This was `holder()` with no argument, taking
    # the module default; once a lock is keyed by the device it holds, that
    # probe reads a lock nobody ever takes and reports "free" while a device
    # is being driven -- the same consumer-left-behind failure this file's
    # `content is None` branch already exists for, one layer up.
    attached = await _mobile_attached_devices()
    lines += _mobile_lock_rows(attached)
    pinned = (ime.manifest_status() or {}).get("content") or {}
    lines.append(
        "- ✅ QA input method " + str(pinned.get("detail") or "pinned")
        if pinned.get("ok")
        else "- ⬜ QA input method not pinned yet, so typing into the app "
        "is unavailable; taps and assertions work"
    )
    # AND WHETHER IT WORKS ON THE DEVICES IN FRONT OF US. The pin above is
    # a fact about this BUILD; it says nothing about whether any attached
    # device can type a character, which is what a tester whose script typed
    # nothing is actually asking.
    if pinned.get("ok"):
        lines += await _mobile_ime_rows(
            [str(s) for s in ((attached or {}).get("content") or [])]
        )


async def _mobile_emulator_lines() -> list:
    """The line(s) for the emulators adb reports as running."""
    lines: list = []
    try:
        # `provisioner` went with the AVD_NAME filter below: this probe no
        # longer asks about ONE hardcoded AVD, so its only two uses in this
        # scope are gone and leaving the import would fail ruff (F401).
        from tools.mobile import emulator, sdk_locator

        # EVERY booted emulator, not just ours. `find_running(AVD_NAME)` filters
        # to the hardcoded AVD, which is the D1 defect -- and it survived here
        # after the device stage was fixed: on 2026-09-04 this line reported
        # "No emulator running" while S25_Ultra_API35 was booted on
        # emulator-5554 and the lane went on to drive it successfully. Telling a
        # tester nothing is running, and that the tool will start one, is the
        # message that made them abort the run in the first place.
        found = await asyncio.wait_for(
            emulator.list_running(), timeout=_reply._MOBILE_PROBE_S
        )
        if found.get("error"):
            # "No emulator running" would be a guess, and the wrong one: the
            # probe itself failed, so this says nothing about what is booted.
            lines.append(
                "- ⚠️ Could not ask adb what is running: " + str(found["error"])[:160]
            )
            return lines
        booted = found.get("content") or []
        if not booted:
            lines.append("- ⬜ No emulator running — `qa_mobile_test` starts one")
        else:
            lines += _running_emulator_rows(booted, sdk_locator)
    except Exception:
        # Includes asyncio.TimeoutError, which IS an Exception on every version
        # this project supports. "not checked" is the honest word here.
        logger.debug("qa-doctor emulator probe skipped", exc_info=True)
        lines.append(
            "- ⬜ Emulator state not checked (the device probe did not answer "
            "in time); `qa_mobile_status` reports it on demand"
        )
    return lines


def _running_emulator_rows(booted: list, sdk_locator) -> list:
    """One line per booted emulator, plus a hint when there are several."""
    lines: list = []
    for item in booted:
        serial = str((item or {}).get("serial") or "")
        named = str((item or {}).get("avd") or "")
        avd = named or "unknown AVD"
        # The producer the machine report's image row reads, so the
        # doctor and the desktop's decrypt opt-in cannot disagree.
        found = sdk_locator.avd_system_image(named) or {}
        image = str(found.get("content") or "")
        lines.append(
            "- ✅ Emulator `"
            + avd
            + "` is running as "
            + serial
            + (" (" + image + ")" if image else "")
        )
    if len(booted) > 1:
        lines.append("  Pass `serial` to `qa_mobile_test` to say which one to use.")
    return lines


def _capability_gate_rows(mobile_state: str) -> list[tuple[str, bool]]:
    """qa-doctor's capability rows, before the RAG row. Reads flags at call time."""
    return [
        # Feature Analysis is a FULL-edition capability only: the
        # test-cases-only edition does not register its tools
        # (2026-08-03), so listing it there advertises something the
        # tester cannot reach. The flag was DELETED 2026-08-14 (batch
        # 8c) and hardcoded ON, so on the full edition this row is now
        # always true -- it is kept because a tester reading qa-doctor
        # wants to know the capability EXISTS, not which flag carried it.
        *(
            []
            if _common._test_cases_only()
            else [
                (
                    "Feature Analysis (always on since 2026-08-14)",
                    _common._feature_analysis_enabled(),
                )
            ]
        ),
        ("Mobile capture (always on since 2026-08-13)", _common._mobile_capture()),
        # The emulator LANE, which is not the same capability as capture
        # above: capture takes a screenshot to ground generation, this
        # runs cases on a device. Named on every edition, on or off, so a
        # tester reading qa-doctor learns the capability exists -- and the
        # label carries no recipe, because the per-install detail is in
        # the section further down and only when the lane is on.
        mobile_gate._mobile_gate_row(mobile_state),
        ("Swagger/OpenAPI links (always on since 2026-08-13)", True),
    ]


async def _corpus_and_dialog_rows() -> list[tuple[str, bool]]:
    """The RAG row (with the observed corpus count) and the wizard-dialogs row."""
    # Say what was OBSERVED, in the label. A bare tick on an empty
    # corpus advertises grounding that this server's own docstring
    # calls a silent no-op, and the count is observable right here.
    _corpus_entries = 0
    try:
        from tools.rag_store import corpus_entry_count

        _corpus_entries = await corpus_entry_count()
    except Exception:
        logger.debug("corpus count skipped", exc_info=True)
    return [
        (
            f"RAG corpus — grounding on {_corpus_entries} past "
            f"case(s) from this install"
            if _corpus_entries
            else "RAG corpus — empty so far, so nothing is "
            "grounded on past work yet; it fills as you generate",
            _common._rag_enabled(),
        ),
        (
            # The seam's own docstring concedes this is client-dependent:
            # "a client that cannot show dialogs is UNAFFECTED... renders
            # the menu". So state what is true of the SERVER, and what the
            # tester sees when their client cannot.
            "Wizard dialogs — offered to every client; one without "
            "dialog support gets the same choices as a numbered menu",
            _common._elicit_enabled(),
        ),
    ]


async def _setup_gate_lines(mobile_state: str) -> list[str]:
    """qa-doctor's rows under "### Feature gates", one ticked line per gate.

    The mobile section and the unfinished-preps note stay in
    `handle_setup_check`: tests/mobile/test_doctor_off_lane_devices.py pins
    the off-lane note beside the disclosure in that function's own source.
    Every flag is a module global read at call time.
    """
    gates = _capability_gate_rows(mobile_state) + await _corpus_and_dialog_rows()
    return [f"- {'✅' if value else '⬜'} {label}" for label, value in gates]


# The headline qa-doctor shows for each reload reason, keyed by the reason
# _schedule_reload records in the marker.
_RELOAD_HEADLINES: dict[str, str] = {
    "update": (
        "🔄 **A new version was just installed.** The server is "
        "reloading now to apply it."
    ),
    "code": (
        "🔄 **Newer code is installed than this server is "
        "running.** Another process updated this install while "
        "this one was already started, so it is serving stale "
        "modules. Reloading now."
    ),
    "config": (
        "🔄 **Configuration changed.** `.env` was edited after "
        "this server started, so the settings this process is "
        "using are the ones it booted with — not what the file "
        "says now. Reloading to apply them."
    ),
}


def _pending_reload_reason(update_status: str) -> str | None:
    """Why this process must reload now, or None; the first match wins.

    "code" (ops-6, bug 2): the trigger once watched ONLY .env, so an update
    applied by ANOTHER process sharing this install dir left this one serving
    stale modules with no signal -- run_update_check already saw the new disk
    and said "up-to-date" (2026-07-29: 1.10.4 on disk while a process from
    08:57 kept serving 1.10.3, so one host-mode flow ran prepare and submit on
    different code). "config": the settings the report renders came from the
    OLD .env, so they must not be shown as the applied config."""
    if update_status in ("updated", "healed"):
        return "update"
    if export._code_changed_since_start():
        return "code"
    if export._env_changed_since_start():
        return "config"
    return None


async def _setup_reload_gate(progress: _common.ProgressCb) -> tuple[str, str | None]:
    """qa-doctor's update check: (update_status, reply when a reload is due).

    Runs only on the dist edition with an update repo; otherwise ("", None).
    ops-8: a scheduled reload ends this process within seconds, so the reply
    is ONLY the honest headline -- see _reloading_message for why splicing a
    note into the full report misled testers."""
    if not (_common._test_cases_only() and _DIST_UPDATE_REPO):
        return "", None
    from tools.updater import run_update_check

    await _common._emit(progress, "⬆️ Checking for the latest release…")
    update_status = await asyncio.to_thread(
        run_update_check,
        force=True,
        repo_override=_DIST_UPDATE_REPO,
        lock_override=True,
    )
    reason = _pending_reload_reason(update_status)
    if reason is None:
        return update_status, None
    _schedule_reload(reason)
    return update_status, _reloading_message(_RELOAD_HEADLINES[reason])


# 2026-08-31: `except (OSError, ValueError)` covers an unreadable or
# unparseable file, but NOT a file holding valid JSON that is not an
# object -- `null` or `[]` reached `.get` and raised AttributeError.
# The outer handler then replaced the ENTIRE report with "Setup
# check failed: 'list' object has no attribute 'get'", so a tester
# on a machine with one torn sidecar lost every other diagnosis in
# the tool that exists to give them one. This file is written by a
# different process and can be truncated mid-write, which is exactly
# how a half-flushed object becomes something else.
def _editor_restart_note(install_dir: Path) -> str:
    restart_note = ""
    try:
        state = json.loads(
            (install_dir / "backups" / "session-state.json").read_text(encoding="utf-8")
        )
        if not isinstance(state, dict):
            raise ValueError("session-state.json is not a JSON object")
        client_v = str(state.get("client_schema_version") or "")
    except (OSError, ValueError, AttributeError, TypeError):
        client_v = ""
    if client_v:
        from tools.updater import _parse_version

        have = _parse_version(client_v)
        need = _parse_version(_TOOL_SCHEMAS_CHANGED_IN)
        if have is not None and need is not None and have < need:
            restart_note = (
                "⚠️ **One-time editor restart needed** — this editor session "
                f"loaded the agent's tool definitions at v{client_v}, but "
                f"they changed in v{_TOOL_SCHEMAS_CHANGED_IN}. Editors do "
                "not refresh tool definitions mid-session: quit and reopen "
                "the editor (Cmd+Q on macOS) to load the latest "
                "capabilities. Everything else updates automatically."
            )
    return restart_note


# A reload that did not take effect is not passive information: the
# server may be serving stale code, so it belongs in the action items.
# Recommended, not blocking -- the server still answers.
# The same hazard on an install that cannot reload itself. Guarded by
# the NEGATION of the reload gate above, deliberately: where the reload
# path runs it has already returned a `_reloading_message`, so reaching
# here at all means no reload was scheduled and the drift would
# otherwise go unreported. See _drift_advisory for why this is a
# message rather than an exit.
def _reload_and_drift_items(reload_action: str, recommended: list[str]) -> None:
    if reload_action:
        recommended.append(reload_action)
    if not (_common._test_cases_only() and _DIST_UPDATE_REPO):
        _drift = _drift_advisory()
        if _drift:
            recommended.append(_drift)


# Host-boomerang migration disclosure (lands in PHASE 1, with the kill
# switch itself). QA_SERVER_LLM_ENABLED=false while ledger rows are still
# unmigrated turns the TICKET-7154 ambiguity gate, bug reports, coaching,
# vision grounding and the eval harness OFF at once -- an operator must
# not have to discover that from behaviour, so it is reported here and in
# a one-time startup WARNING. It also changes what "backend broken" means:
# with the server LLM retired, test-case generation runs on the tester's
# own chat model, so an unusable backend is no longer a blocker.
# Phase-6 preparation (2026-08-02): the CALM branch became
# reachable in production for the first time when residue R4
# emptied UNMIGRATED_PATHS, and it is INFORMATION, not an action
# item. Left in `recommended` it would make every correctly
# retired install report "Ready, with warnings" forever -- the
# verdict below is derived from that list being non-empty -- which
# trains an operator to ignore the one list meant to demand
# attention. `optional` is where a true, no-action-needed
# statement belongs. Both degraded branches are unchanged and
# still land in `recommended`.
# 2026-08-15 (dead-code deletion batches D2 and D3): the three
# per-mode entries that stood here -- `maestro_healer.classify`,
# `maestro_explorer.decide` and `web_runner.verify` -- were DELETED
# with the modules and the modes they disclosed (tools/maestro_*.py
# in D2, tools/web_runner.py in D3). Naming a loss for a mode that
# no longer exists is the same dishonesty as hiding a real one,
# which is this block's whole discipline. Their ledger rows are
# HISTORY and stay in docs/LLM_MIGRATION_INVENTORY.md and in
# host_llm.LEDGER_IDS (ids never leave it; the set is pinned at 24
# in five test files), so an allow-list typo on any of them is
# still detectable.
# Residue sub-phase R3: `image_description.describe_images` is a terminal
# `disabled (disclosed)` row, so it has LEFT UNMIGRATED_PATHS and the
# generic disclosure line above no longer names it -- but its surviving
# caller is TESTER-FACING (the `mobile` / `jira_mobile` modes of
# qa_feature_analysis describe captured device screens through this
# server's own ask_vision), so the 5b/5c convention applies: name the
# mode and the exact allow-list id HERE rather than let a tester discover
# it standing at a device. Deliberately NOT inside the
# `not _test_cases_only()` block above, on the grounds that the dist
# edition ships feature_analysis.py AND device_manager.py so the loss was
# just as real there.
# CORRECTION (2026-08-03): that rationale no longer holds. The dist does
# not REGISTER qa_feature_analysis / qa_submit_feature_analysis at all,
# so naming a mode a dist tester cannot invoke would invent a loss nobody
# suffers -- the precise thing this module's disclosure discipline
# forbids. It therefore moves INSIDE the edition gate; the full edition
# is byte-identical. The sibling vision row
# `ui_extractor.describe_via_vision` still gets NO item, deliberately:
# it is `migrated` (the rendered page screenshot rides to the host's own
# model through IMAGE_JOB on the only route that reaches it), so an item
# would invent a loss no tester suffers.
# 2026-08-15: the per-mode vision-loss item was DELETED, not disabled.
# qa_feature_analysis's mobile modes no longer need a server vision
# call at all -- the captured screens ride to the tester's own
# multimodal model as MCP image content -- so there is no loss to
# disclose, and QA_SERVER_LLM_ALLOW=image_description.describe_images
# would restore nothing (that caller is gone). Claiming a loss no
# tester suffers is the same dishonesty as hiding a real one.
# Phase 5d: the Maestro step-translation flag was already INERT on
# the MCP surface -- its only caller was the retired Chainlit export
# path, so qa-doctor had to report the FLAG ITSELF as having no
# effect. On 2026-08-13 QA_MAESTRO_TRANSLATE_ENABLED was DELETED and
# hardcoded OFF (flag-surface reduction, batch 8a), so there is no
# longer a configuration surprise to disclose, and that advisory is
# gone with it. tools.maestro_exporter.translate_enabled() is the
# seam; re-wiring the export path is still a separate plan.
# 2026-08-16 (dead-code deletion P2-G2b): the LLM-backend BLOCKER, the
# Environment backend line and both ANTHROPIC_API_KEY placeholder items
# stood here. All three reported on a capability this product no longer
# has -- P2-G2c deletes `llm.py`'s coroutines and all three backends, so
# nothing in the tree can reach one. The blocker was the harmful part:
# it told a tester whose Claude CLI session had expired that "nothing
# generates without it" while everything generated fine, because the
# generation runs in their own chat. Reporting a fault that cannot
# affect any tester flow is the same over-claim this function's
# disclosure discipline forbids in the other direction.
#
# The host-boomerang migration disclosure above is a DIFFERENT seam and
# is untouched: it reports what this server does not do, not whether a
# backend is reachable.
# D4 follow-up (2026-08-25): the tester-facing half of the
# "rate-limited" status. "" on every other status, so a healthy
# run is byte-identical.
def _standing_findings(
    update_status: Any,
    restart_note: str,
    workspace_roots: list[Path] | None,
    recommended: list[str],
    optional: list[str],
) -> None:
    from tools import host_llm as _host_llm

    _server_llm_note, _server_llm_degraded = _host_llm.disclosure_state()
    if _server_llm_note and _server_llm_degraded:
        recommended.append(_server_llm_note)
    elif _server_llm_note:
        optional.append(_server_llm_note)
    if restart_note:
        recommended.append(
            "Quit and reopen the editor once so it reloads the agent's "
            "latest tool definitions."
        )

    _client_registry_warnings(workspace_roots, recommended)
    _rate_limit_note = host_check._update_rate_limit_advisory(update_status)
    if _rate_limit_note:
        recommended.append(_rate_limit_note)


# 2026-08-01: there is nothing Jira-shaped left for this server to
# check. Jira is read through the CALLING AGENT's own Atlassian MCP
# connection (OAuth), which a stdio subprocess cannot observe -- so
# printing "configured" or "verified" here would be a guess, and a
# confident wrong answer is worse than none. State what is true and
# point at the one place that gives real guidance.
# 2026-08-03: warn BEFORE a suite is built when more than one qa server is
# registered. The packaged install sits in the USER configs while the
# project's own .mcp.json registers a DEV checkout, so opening the repo puts
# two live servers in front of the agent -- which is how one real run
# prepared on v0.1.0 and finalized on v1.34.0. Recommended, not optional:
# the two installs have separate .env files, so the flags the suite was
# prepared under are not the flags it was finalized under. Silent when every
# client points at the SAME install, which is the normal case.
# Cursor-hardening v1.97.0 (item 5): warn when the tester's open
# workspace CONTAINS this server's own source tree -- that is what
# lets an agent import handlers/ directly or spawn its
# own copy of this server, bypassing the registered tool surface and
# its dispatch guard entirely (2026-09-25 Cursor audit).
def _client_registry_warnings(
    workspace_roots: list[Path] | None, recommended: list[str]
) -> None:
    try:
        from tools.client_registry import split_server_warning

        _split = split_server_warning(workspace_roots=workspace_roots)
        if _split:
            recommended.append(_split)
    except Exception:
        logger.debug("split-server check failed", exc_info=True)
    try:
        from tools.client_registry import workspace_contains_server_warning

        _ws_warning = workspace_contains_server_warning(workspace_roots=workspace_roots)
        if _ws_warning:
            recommended.append(_ws_warning)
    except Exception:
        logger.debug("workspace-contains-server check failed", exc_info=True)


# 2026-09-01 (item A): the verdict recorded by a previous
# qa_configure_jira probe, for THIS client. Read ONCE, here, because
# three separate sites need it -- the on-disk hint below, the
# Integrations row, and the report headline -- and a report whose three
# Jira statements disagree with each other is worse than one guess.
#
# Guarded: verify_store never raises, and this makes a broken read
# degrade to "never verified", which is exactly the pre-A behaviour. It
# must not reach this function's outer `except`, which would replace the
# ENTIRE report with a failure line over a verdict lookup.
async def _load_verdict_view() -> dict | None:
    _verdict_view = None
    try:
        from tools.verify_store import load_verdict

        _verdict_view = (await load_verdict()).get("content")
    except Exception:
        logger.debug("atlassian verdict lookup skipped", exc_info=True)
    return _verdict_view


# Provenance is checked BEFORE freshness: a day-old verdict from the
# identity-only probe and a week-old verdict from the current one need
# different words, and only the first can be fixed by re-running a
# better check.
def _atlassian_verdict_state(view: dict | None) -> str:
    """Name the recorded Atlassian verdict: provenance first, then freshness."""
    if not view:
        _verdict_state = "unverified"
    elif not view.get("verified"):
        # A negative verdict whose ACCESS half was checked means the sign-in
        # worked and reached the wrong Atlassian account -- a different
        # sentence and a different remedy from "not connected".
        _verdict_state = "wrong_site" if view.get("site_checked") else "failed"
    elif not view.get("current_probe"):
        _verdict_state = "probe_outdated"
    elif not view.get("fresh"):
        _verdict_state = "stale"
    elif not view.get("site_checked"):
        _verdict_state = "access_unconfirmed"
    else:
        _verdict_state = "verified"
    return _verdict_state


# BEFORE connect_hint_line, deliberately: that helper re-reads the config
# from disk, so a fresh write flips its message to "already configured on
# disk" instead of telling the tester to add what was just added.
async def _atlassian_part(
    workspace_roots: list[Path] | None,
    verdict_view: dict | None,
    fix: bool,
    recommended: list[str],
    optional: list[str],
) -> tuple[bool, list[str]]:
    """Run the Atlassian check/fix when Jira is in use; queue its advice."""
    # G5: Jira is checked, mentioned and (with fix=true) written ONLY when it
    # is already in use. A tester with no Jira sees no Jira line, and fix=true
    # writes nothing for them; settings or an entry or a verdict keep today's
    # behaviour, fix path included.
    _jira_used = host_check._jira_in_use(workspace_roots, verdict_view)
    if _jira_used:
        _atlassian_lines, _atlassian_advisories = await host_check._atlassian_autofix(
            fix=fix
        )
    else:
        _atlassian_lines, _atlassian_advisories = [], []
    recommended.extend(_atlassian_advisories)
    # verify_offered: the on-disk hint returns "" rather than shrugging
    # beside an answer this report has already given -- the Integrations
    # row states the Jira state in every verdict, including "checked when
    # you first ask for a ticket". The CONNECT wording (no entry on disk)
    # is unaffected and still appears.
    _hint = (
        connect_hint_line(workspace_roots=workspace_roots, verify_offered=True)
        if _jira_used
        else ""
    )
    if _hint:
        optional.append(_hint)
    return _jira_used, _atlassian_lines


# A real write, not os.access on whichever ancestor happens to
# exist: that answered a different question ('is some parent
# writable') and printed a green tick for it. os.access also
# consults permission bits only, which is the wrong answer on a
# read-only mount or an ACL-governed share.
# Not created yet: the honest claim is about the parent, and the
# label says so rather than implying the folder is ready.
def _export_write_probe(target: Path) -> tuple[bool, bool]:
    """Probe the export folder (or its nearest parent) for writability."""
    export_exists = target.is_dir()
    export_ok = False
    if export_exists:
        _probe_file = target / f".qa-write-probe-{os.getpid()}"
        try:
            _probe_file.write_text("", encoding="utf-8")
            export_ok = True
        except Exception:
            export_ok = False
        finally:
            try:
                _probe_file.unlink()
            except Exception:
                logger.debug("export write-probe cleanup failed", exc_info=True)
    else:
        _parent = target
        while not _parent.exists() and _parent.parent != _parent:
            _parent = _parent.parent
        export_ok = os.access(_parent, os.W_OK)
    return export_exists, export_ok


def _export_status_suffix(export_ok: bool, export_exists: bool) -> str:
    """Return the status clause appended to the export-directory row."""
    return (
        (
            " — checked by writing to it just now"
            if export_exists
            else " — the folder does not exist yet and will be "
            "created on the first export; its parent is writable"
        )
        if export_ok
        else (
            " — not writable: a test file could not be created there just now"
            if export_exists
            else " — not writable: the folder does not exist and "
            "its parent cannot be written to either"
        )
    )


# K5 (2026-08-10): this said "you choose where each file is
# saved" -- but F1a gates the save-folder dialog on an
# UNRESOLVED export dir, so in THIS branch the tester is never
# asked. QA_EXPORT_DIR is the control here, and
# qa_export_suite(output_dir=...) is the per-call override.
def _export_dir_line(recommended: list[str]) -> str:
    """Build the Excel auto-export row; queue a fix when it is not writable."""
    export_line = ""
    export_dir = export._resolved_export_dir()
    if export_dir:
        dest = Path(export_dir).expanduser()
        target = dest if dest.is_absolute() else Path.cwd() / dest
        export_exists, export_ok = _export_write_probe(target)
        export_line = (
            f"- {'✅' if export_ok else '⚠️'} **Excel auto-export** — "
            f"files are saved to `{dest}` (set `QA_EXPORT_DIR`, or pass "
            "`output_dir` to `qa_export_suite`)"
            + _export_status_suffix(export_ok, export_exists)
        )
        if not export_ok:
            recommended.append(
                f"Make the export directory `{dest}` writable (or "
                "change QA_EXPORT_DIR) — until then generated Excel "
                "files fall back to a temp folder."
            )
    else:
        export_line = (
            "- ✅ **Excel auto-export** — you choose where each file "
            "is saved (fallback: secure temp directory)"
        )
    return export_line


async def _env_heal_fetch(install_dir: Path, fix: bool, recommended: list[str]) -> dict:
    """Heal (fix=True) or only look for (fix=False) superseded .env defaults."""
    from tools.env_heal import find_superseded, heal_env

    if fix:
        _heal = await asyncio.to_thread(heal_env, Path(install_dir))
    else:
        # Read-only default: look, never write, and stay silent when
        # nothing is outdated.
        _heal = await asyncio.to_thread(find_superseded, Path(install_dir))
        if _heal.get("changed"):
            _stale = ", ".join(
                f"`{_key}` (`{_old}` → `{_new}`)"
                for _key, _old, _new, _why in _heal["changed"]
            )
            recommended.append(
                f"Outdated .env setting(s): {_stale}. Call qa-doctor with "
                "fix=true to update them (the old .env is backed up to "
                ".env.bak-*)."
            )
            _heal = {"error": _heal.get("error")}
    return _heal


def _env_heal_report(heal: dict, heal_lines: list[str], recommended: list[str]) -> None:
    """Render a repaired .env (or a failed check) into the report lists."""
    if heal.get("changed"):
        heal_lines.append("### Configuration repaired")
        heal_lines.append("")
        for _key, _old, _new, _why in heal["changed"]:
            heal_lines.append(f"- `{_key}`: `{_old}` → `{_new}` — {_why}")
        heal_lines.append("")
        heal_lines.append(
            f"_Backup: `{heal.get('backup') or 'n/a'}`. These take effect "
            "when the MCP server restarts — quit and reopen your editor._"
        )
        heal_lines.append("")
        recommended.append(
            "Restart the MCP server (quit + reopen your editor) so the "
            f"{len(heal['changed'])} repaired setting(s) take effect."
        )
    elif heal.get("error"):
        recommended.append(
            f"Could not check the .env for stale settings: {heal['error']}"
        )


# Repair superseded .env defaults BEFORE the verdict, so the report
# reflects the file as it now stands. Never fatal: a failure is reported
# as a recommendation and the rest of the check proceeds.
async def _env_heal_lines(
    install_dir: Path, fix: bool, recommended: list[str]
) -> list[str]:
    """Run the .env self-heal step; never fatal, partial lines survive."""
    heal_lines: list[str] = []
    try:
        _heal = await _env_heal_fetch(install_dir, fix, recommended)
        _env_heal_report(_heal, heal_lines, recommended)
    except Exception:
        logger.debug("env self-heal step skipped", exc_info=True)
    return heal_lines


# Flag-governance disclosure (2026-08-13): tools/flag_registry.py is a
# PRIVATE-repo-only module (not in scripts/build_dist.TOOL_FILES), so a
# public qa-agent-pro install never runs this repo's own test suite and
# would otherwise never learn that an `experiment` flag's review_by
# date has passed -- CLAUDE.md says "holding a flag at OFF indefinitely
# is not an option", but nothing besides pytest enforced that until
# now. Optional, never blocking: an expired review date is maintainer
# housekeeping, never something that should stop a tester from
# generating test cases today. The import is local so a public dist
# build (which never ships this module) is byte-identical without it,
# and the catch is broad -- matching the env self-heal block right
# above -- so a bug in flag_registry degrades to a skipped optional
# line instead of escaping to this function's OUTER except Exception
# and discarding the whole report.
def _flag_expiry_item(optional: list[str]) -> None:
    """Add an optional note when feature flags are past their review-by date."""
    try:
        from datetime import date

        from tools import flag_registry

        _expired = flag_registry.expiring_on_or_before(date.today().isoformat())
        if _expired:
            _names = ", ".join(f"`{name.upper()}`" for name, _e in _expired[:5])
            _more = f" (+{len(_expired) - 5} more)" if len(_expired) > 5 else ""
            optional.append(
                f"{len(_expired)} feature flag(s) are past their review-by "
                f"date: {_names}{_more}. Promote each to always-on or delete "
                "it per the CLAUDE.md flag policy — see docs/FEATURE_FLAGS.md."
            )
    except Exception:
        logger.debug("flag-registry expiry check skipped", exc_info=True)


# Fix 7 / M3 (2026-08-03): the ONLY discoverable path to registration.
# QA_AUTO_REGISTER_CLIENTS defaults OFF (it writes outside the install
# dir), and even ON it cannot bootstrap the FIRST client -- if no editor is
# registered, nothing launches this server, so no startup pass ever runs.
# Without this line a tester who installs another editor has no way to
# learn that connect.sh needs re-running, which is the whole gap Fix 7 is
# about. Optional, not a blocker: the reader is, by definition, running in
# a client that IS already registered.
_CONNECT_SCRIPT_HINT: str = (
    "Installed another editor since setting this up? Run "
    "`~/qa-agent-pro/connect.sh` to register this server with it -- "
    "registration otherwise happens only once, during install. It is "
    "idempotent, preserves your other MCP servers, and backs up any file "
    "it changes."
)


class _SetupHead(NamedTuple):
    verdict: str
    app_version: str
    restart_note: str
    reload_note: str
    heal_lines: list
    atlassian_lines: list
    py_version: str
    py_ok: bool
    export_line: str
    jira_used: bool
    jira_status_line: str
    tool_paths: dict


# 2026-09-25: qa-doctor NEVER asks the agent to probe Atlassian. The
# probe call is what opens the OAuth sign-in, and a readiness check must
# not push the tester through an authorization they did not ask for.
# The connection is checked on demand instead: the first time the
# tester asks for Jira data, build_fetch_directive's getJiraIssue call
# either works or falls through to connect_steps(). Known-broken states
# still warn, because they say something the tester can act on.
#
# This block is deliberately ABOVE the headline: the failed states
# append to `recommended`, which _overall_verdict counts.
#
# Same branch, so ONE derivation with two consumers: the action
# item says what to do, `limited` makes the headline say what has
# stopped working.
#
# Known-broken and actionable to the TESTER. Not a blocker:
# generation from a typed feature description is unaffected.
def _jira_state_advisories(
    state: str, view: dict | None, recommended: list[str], limited: list[str]
) -> None:
    _on_demand = (
        "I'll re-check it the next time you ask me for a Jira ticket, and "
        "walk you through signing in then if it is still not connected."
    )
    if state == "wrong_site":
        limited.append(
            "importing Jira tickets (the connected Atlassian account "
            "cannot reach this tester's site)"
        )
        recommended.append(
            "The Atlassian account signed in here cannot reach this "
            "tester's Jira site "
            f"({(view or {}).get('at')}), so ticket URLs will fail "
            "until the right account is connected in your editor. Test "
            "generation from a typed feature description is unaffected. " + _on_demand
        )
    elif state == "failed":
        limited.append(
            "importing Jira tickets (the Atlassian connection failed its last check)"
        )
        recommended.append(
            "The Atlassian (Jira) connection failed its last check "
            f"({(view or {}).get('at')}), so ticket URLs will not "
            "work until it is reconnected in your editor. Test generation "
            "from a typed feature description is unaffected. " + _on_demand
        )


def _setup_report_head(head: _SetupHead) -> list[str]:
    return [
        "## Setup check",
        "",
        f"**Overall:** {head.verdict}",
        "",
        *([f"**App version:** v{head.app_version}", ""] if head.app_version else []),
        *([head.restart_note, ""] if head.restart_note else []),
        *([head.reload_note, ""] if head.reload_note else []),
        *head.heal_lines,
        *head.atlassian_lines,
        "### Environment",
        f"- {'✅' if head.py_ok else '❌'} **Python** {head.py_version}"
        + ("" if head.py_ok else " — 3.10 or newer required"),
        *([head.export_line] if head.export_line else []),
        "",
        *(
            ["### Integrations", "- " + head.jira_status_line, ""]
            if head.jira_used
            else []
        ),
        *host_check._tooling_lines(head.tool_paths),
        "",
        "### Feature gates",
    ]


# Item 2b: unfinished host-mode preps (disclosure only, flag-gated;
# empty string when QA_PREP_DISCLOSE_UNFINISHED is off or none exist).
async def _unfinished_and_ac_lines() -> list[str]:
    _unfinished = await prep_status._unfinished_preps_note()
    lines: list[str] = []
    if _unfinished:
        lines += ["", "### Unfinished host-mode preps", _unfinished.rstrip()]
    lines += ["", *host_check._ac_field_section()]
    return lines


def _action_item_lines(
    blockers: list[str], recommended: list[str], optional: list[str]
) -> list[str]:
    items = (
        [("Fix now", item) for item in blockers]
        + [("Recommended", item) for item in recommended]
        + [("Optional", item) for item in optional]
    )
    lines: list[str] = []
    if items:
        lines += ["", "### Action items"]
        for idx, (tag, text) in enumerate(items, 1):
            lines.append(f"{idx}. **{tag}:** {text}")
    return lines


# [reload_note: why it is read AFTER the reload gate]
# Report the PREVIOUS reload's outcome ("" / "" when there was none).
# Deliberately AFTER the block above: a call that schedules a new reload
# has already returned, and its marker overwrites the old one, so the
# next settled call always reports the most recent reload.
#
# [the `limited` list]
# The FOURTH list, and it is not a fourth severity: `limited` names
# what this machine CANNOT DO and feeds no count in `_overall_verdict`
# (see there). Everything in it is true of the machine, actionable
# nowhere in this report, and invisible in the headline until now.
#
# [_tool_paths / _mobile_state, read once]
# The lane's THREE states, from the one producer. Read once: the
# Feature-gates row, the mobile section and the headline all branch on
# this same value, so they cannot describe the lane three ways.
#
# [the lane in the headline (orphan comment after `limited +=`)]
# The lane reaches the HEADLINE only where this machine could plausibly
# run it today: modules on disk, the switch off, and `adb` already
# here. Two reasons for that bound. Where `adb` is missing the gap
# above already names Android tooling, so exactly one Android-shaped
# phrase can ever appear; and where the lane is merely off on a machine
# with no Android tooling at all, a permanent headline suffix on every
# report forever is the nag-with-no-exit this function was corrected
# for on 2026-09-01. The DISCLOSURE itself is unconditional -- it is in
# the Feature-gates row and in the section below on every install.
#
# [the mobile lane section, on branch]
# The mobile lane's own section: SDK, IME and emulator state. Empty
# (not a "disabled" line) when the lane is off -- the gate row above
# already said so, and a second telling is noise on every install that
# will never use it.
# BRANCH ON THE STATE, never on the section being empty. "An empty list
# means the lane is off" is an inference, and a future early return
# inside `_mobile_doctor_section` would silently turn a running lane
# into an off-lane disclosure. `_mobile_doctor_section` keeps its own
# `_mobile_lane_enabled()` guard as well: two independent checks of the
# same state, neither of which is load-bearing alone.
#
# [the mobile lane section, off branch]
# The lane is present-but-off, or not in this build at all. It used
# to render as one blank checkbox that named no setting and no
# capability; a tester with an Android phone attached read that and
# learned nothing they could act on.
#
# [the attached-device note in the off branch]
# A tester with a phone plugged in and the flag off used to read
# a block that never mentioned their device. This names it --
# but only from an adb server that is ALREADY up, so the
# disclosure still starts nothing.
async def handle_setup_check(
    *,
    progress: _common.ProgressCb = None,
    workspace_roots: list[Path] | None = None,
    fix: bool = False,
) -> str:
    """Machine-readiness report for tester onboarding: environment, LLM
    backend auth, integrations, CLI tooling, and feature gates — summarised
    into an overall verdict plus concrete action items. Never raises.

    **Read-only by default** (2026-09-25, P2-10): pass ``fix=True`` to repair
    superseded defaults in the install's own `.env` and write a missing
    `atlassian` MCP entry to the editor's config. With ``fix=False`` neither
    write runs; both are reported as "not checked, call with fix=true"
    instead. Neither touches the unattended startup pass.

    ``workspace_roots`` is the tester's OPEN workspace folder(s) as reported by
    the MCP ``roots`` capability. It is resolved in ``mcp_server.qa-doctor``
    (that is where the client Context lives, and tools/jira_mcp.py makes no
    protocol call of its own) and is forwarded ONLY to ``connect_hint_line``, so
    the on-disk `atlassian` check looks in the project the tester actually has
    open instead of guessing. ``None`` = a client without ``roots`` support: the
    hint falls back to its previous candidate chain."""
    await _common._audit("mcp_setup_check")
    try:
        run = await _setup_collect(progress, workspace_roots, fix)
        if isinstance(run, str):
            return run
        lines = _setup_report_head(run.head())
        lines += await _setup_gate_lines(run.mobile_state)
        lines += await _setup_mobile_lines(run.mobile_state, run.tool_paths)
        lines += await _unfinished_and_ac_lines()
        lines += _action_item_lines(run.blockers, run.recommended, run.optional)
        return "\n".join(lines)
    except Exception as exc:
        logger.exception("handle_setup_check failed")
        return f"⚠️ Setup check failed: {exc}"


@dataclass
class _SetupRun:
    """Everything one qa-doctor run learns before the report is rendered.

    Findings are classified as blocking / recommended / optional so the report
    opens with a single actionable verdict; ``limited`` names what this machine
    cannot do and is appended to the headline, never counted.
    """

    blockers: list[str] = field(default_factory=list)
    recommended: list[str] = field(default_factory=list)
    optional: list[str] = field(default_factory=list)
    limited: list[str] = field(default_factory=list)
    # THE ONE LOOKUP for the optional binaries, read twice -- by the
    # Command-line tooling rows and by `limited` -- and derived once.
    tool_paths: dict = field(default_factory=dict)
    mobile_state: str = ""
    verdict: str = ""
    app_version: str = ""
    restart_note: str = ""
    reload_note: str = ""
    heal_lines: list = field(default_factory=list)
    atlassian_lines: list = field(default_factory=list)
    py_version: str = ""
    py_ok: bool = True
    export_line: str = ""
    jira_used: bool = False
    jira_status_line: str = ""

    def head(self) -> _SetupHead:
        """The report-head tuple, in _SetupHead field order."""
        return _SetupHead(
            self.verdict,
            self.app_version,
            self.restart_note,
            self.reload_note,
            self.heal_lines,
            self.atlassian_lines,
            self.py_version,
            self.py_ok,
            self.export_line,
            self.jira_used,
            self.jira_status_line,
            self.tool_paths,
        )


async def _setup_collect(
    progress: _common.ProgressCb, workspace_roots: list[Path] | None, fix: bool
) -> "_SetupRun | str":
    """Run the checks; returns the gathered run, or a reply that ends it early."""
    from tools.updater import _INSTALL_DIR, _local_version

    # Bound BEFORE the edition gate: the advisory below reads it on
    # every edition, and a full checkout never runs the check at all.
    update_status, reload_reply = await _setup_reload_gate(progress)
    if reload_reply is not None:
        return reload_reply
    run = _SetupRun()
    run.reload_note, reload_action = _reload_outcome(_consume_reload_marker())
    await _common._emit(progress, "🔎 Validating the environment…")
    run.app_version = _local_version(_INSTALL_DIR)
    run.restart_note = _editor_restart_note(_INSTALL_DIR)
    run.tool_paths = host_check._optional_tool_paths()
    run.mobile_state = mobile_gate._mobile_lane_state()
    run.limited += host_check._tooling_gaps(run.tool_paths)
    _reload_and_drift_items(reload_action, run.recommended)
    run.py_version = sys.version.split()[0]
    run.py_ok = sys.version_info >= (3, 10)
    if not run.py_ok:
        run.blockers.append(
            f"Upgrade Python to 3.10 or newer (currently {run.py_version})."
        )
    _standing_findings(
        update_status, run.restart_note, workspace_roots, run.recommended, run.optional
    )
    await _setup_collect_jira(run, _INSTALL_DIR, workspace_roots, fix)
    return run


async def _setup_collect_jira(
    run: _SetupRun, install_dir: Path, workspace_roots: list[Path] | None, fix: bool
) -> None:
    """Jira, export-dir and env-heal findings, then the overall verdict."""
    view = await _load_verdict_view()
    state = _atlassian_verdict_state(view)
    run.jira_used, run.atlassian_lines = await _atlassian_part(
        workspace_roots, view, fix, run.recommended, run.optional
    )
    run.optional.append(_CONNECT_SCRIPT_HINT)
    run.jira_status_line = host_check._jira_status_text(state, view)
    run.export_line = _export_dir_line(run.recommended)
    run.heal_lines = await _env_heal_lines(install_dir, fix, run.recommended)
    _flag_expiry_item(run.optional)
    _jira_state_advisories(state, view, run.recommended, run.limited)
    # Jira is never an unsettled question here: it is checked on demand.
    run.verdict = host_check._overall_verdict(
        len(run.blockers),
        len(run.recommended),
        0,
        # KEYWORD, and pinned at this call site by its own test: the
        # parameter has a default, so nothing about the function alone can
        # catch this argument being dropped.
        limited=tuple(run.limited),
    )


async def _setup_mobile_lines(mobile_state: str, tool_paths: dict) -> list[str]:
    """The mobile section: the doctor section when ON, the disclosure when OFF."""
    if mobile_state == "on":
        mobile_lines = await _mobile_doctor_section()
        return ["", *mobile_lines] if mobile_lines else []
    return [
        "",
        *mobile_gate._mobile_lane_disclosure(mobile_state, tool_paths.get("adb")),
        *await _mobile_off_attached_note(),
    ]
