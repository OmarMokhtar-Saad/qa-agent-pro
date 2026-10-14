"""Moved from handlers (refactor P2): mobile_gate."""

from __future__ import annotations

import asyncio
import logging
import socket

from config.settings_guard import never_raise
from handlers import _common, _reply, mobile_gate_android
from handlers.mobile_gate_android import _non_android_refusal  # noqa: F401
from tools.device_manager import list_devices
from tools.process_env import get_env

logger = logging.getLogger("tools.mcp_handlers")

_MOBILE_LANE_ABSENT = (
    "\u2139\ufe0f This build does not include the mobile emulator lane's "
    "modules, so there is nothing here to switch on -- a setting cannot enable "
    "code that is not installed. An install built with the lane included is "
    "what changes this. Nothing else here is affected."
)


def mobile_lane_state() -> str:
    """``"on"`` or ``"absent"``. Never raises.

    ONE PRODUCER, and it invents no condition: it CALLS the gate that decides
    registration rather than re-expressing it, so the two cannot drift. There
    is no settings-owned off state: the lane is on wherever its modules are on
    disk, and ``absent`` is the build's, which nothing on the machine changes.

    Consumers, all of them: ``_mobile_lane_off_message`` (the three tool-call
    refusals), ``_mobile_gate_row`` (qa-doctor's Feature-gates row) and
    ``mobile_lane_disclosure`` (qa-doctor's own section). ``mcp_server``'s
    registration and ``tools/guidance.py`` are deliberately NOT consumers --
    both only ever ask "may I name these tools", and the boolean gate is the
    right answer to that question.
    """
    return "on" if _common.mobile_lane_enabled() else "absent"


def _mobile_lane_off_message() -> str:
    """The refusal a mobile tool returns when the lane will not run.

    The only off state is ``absent``: a build with no ``tools/mobile`` gets
    ``_MOBILE_LANE_ABSENT``, which names no setting, because no setting can
    turn the lane on or off.

    Total on purpose. The ``"on"`` branch is unreachable from all three call
    sites (each is guarded by ``if not mobile_lane_enabled():``) but a
    function whose whole job is to produce tester-facing words must have a true
    sentence for every state rather than an empty string for one.
    """
    state = mobile_lane_state()
    if state == "absent":
        return _MOBILE_LANE_ABSENT
    return (
        "\u2139\ufe0f The mobile emulator lane is available on this install. "
        "Nothing was refused -- call the mobile tool again."
    )


# qa-doctor's Feature-gates row for the emulator lane: LABEL and tick.
#
# The row read "Mobile emulator lane (opt-in kill-switch)" beside a blank
# box. That names no setting, describes no capability and offers no action:
# a Windows tester with an Android phone attached read it, learned the lane
# existed but not what it was or how to reach it, and hand-wrote adb scripts
# for two hours instead. The label now says what the lane DOES and, where
# that is true, what to change to get it.
#
# NAMES NO TOOL, in any state. The lane's three tools are not registered in
# either off state, and a report that prints a call the tester cannot make is
# the same class of untrue tester-facing text as the row it replaces. The
# capability is described in plain words instead.
def _mobile_gate_row(state: str) -> tuple[str, bool]:
    """qa-doctor's Feature-gates row for the emulator lane: LABEL and tick."""
    if state == "on":
        return (
            "Mobile emulator lane \u2014 on: it runs your test cases on an "
            "attached Android device or an emulator and writes an HTML report",
            True,
        )
    if state == "absent":
        return (
            "Mobile emulator lane \u2014 not included in this build. It runs "
            "test cases on an Android device, but its modules are not "
            "installed here, so nothing on this machine can switch it on",
            False,
        )
    return _mobile_gate_row("absent")


# qa-doctor's section for a lane that is NOT running. Never raises.
#
# STATIC TEXT AND ONE ALREADY-COMPUTED PATH. It makes no subprocess call and
# starts no daemon, and that is a rule rather than an optimisation: the
# kill-switch is OFF in this branch, and running `adb devices` here to say
# "you have a phone attached" would start an adb server on a machine whose
# operator switched this lane off -- a side effect produced by a DISCLOSURE.
# The signal is taken from ``optional_tool_paths()`` instead, a PATH lookup
# that starts nothing and is the same value the tooling row above prints, so
# the two cannot disagree.
#
# Empty for ``"on"``: that state is served by ``_mobile_doctor_section``,
# which probes because a running lane is what the tester asked for.
def mobile_lane_disclosure(state: str, adb_path: str | None) -> list[str]:
    """qa-doctor's section for a lane that is NOT running. Never raises."""
    if state == "on":
        return []
    if state == "absent":
        return [
            "### Mobile emulator lane",
            "- \u2b1c Not included in this build, so there is nothing here to "
            "turn on. Test-case generation, exports and device listing are "
            "unaffected.",
        ]
    return mobile_lane_disclosure("absent", adb_path)


#: How long qa-doctor's keyboard STATE read may take per device. Not the one
#: probe above: ``ime_session.state`` makes up to four adb calls (package list,
#: active input method, ``pm path``, ``sha256sum``), and 8 s cut the build check
#: off on a slow device, which the row then reported as "could not check".
MOBILE_IME_STATE_S = 20.0


# The refusal when *serial* names a device this lane cannot drive, else "".
#
# THE MOBILE LANE ONLY, and that is the whole design. ``qa_list_devices``
# keeps every platform and ``qa_capture_screens`` keeps full iOS support
# (simctl, devicectl, the idevicescreenshot fallback, app enumeration, both
# screenshot paths); neither ``shape_devices`` nor
# ``device_manager.list_devices`` is touched, because both are SHARED
# producers and an iOS row is the right answer for their other readers. This
# is the emulator lane's own device-selection path, which replays uiautomator
# scripts over `adb` and can drive nothing else.
#
# POSITIVE IDENTIFICATION ONLY. It refuses when live discovery names this
# exact id as a non-Android device, and in NO other case: an unknown id, a
# probe that fails or times out, or no discovery at all returns "" and the
# call proceeds exactly as it does today. That direction is deliberate -- an
# emulator that is still booting is in no list yet, and refusing it would be a
# new defect traded for the old noise.
async def _mobile_non_android(serial: str) -> str:
    """The refusal when *serial* names a device this lane cannot drive, else ""."""
    wanted = str(serial or "").strip()
    if not wanted:
        return ""
    with never_raise(
        logger,
        "mobile lane platform probe did not finish",
        level=logging.INFO,
        exc_info=True,
    ) as g:
        found = await asyncio.wait_for(list_devices(), timeout=_reply.MOBILE_PROBE_S)
    if g.failed:
        return ""
    return mobile_gate_android._refusal_for(found, wanted)


#: How long the "is an adb server already up?" connect may take. It is a LOCAL
#: socket connect to a port that is either listening or not; anything slower is
#: a wedged machine, and qa-doctor is what a tester runs when nothing works.
_ADB_SERVER_PROBE_S = 0.5


# Is an adb server LISTENING on this machine right now? Never raises.
#
# Used only by the lane-OFF disclosure, and the distinction it draws is why it
# exists at all. `mobile_lane_disclosure`'s rule is that an off lane starts
# nothing: `adb devices` there would START an adb server on a machine whose
# operator switched this lane off. A TCP connect starts nothing -- it either
# finds a server the tester already has or it does not -- so asking a server
# already up costs the operator nothing they have not already paid for.
def adb_server_already_running() -> bool:
    """Is an adb server LISTENING on this machine right now? Never raises."""

    try:
        port = int(str(get_env("ANDROID_ADB_SERVER_PORT") or "5037").strip())
    except (ValueError, OverflowError):
        port = 5037
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=_ADB_SERVER_PROBE_S):
            return True
    except OSError:
        return False


def _adb_server_already_running() -> bool:
    """Alias for ``setup_mobile_off`` until WX moves it to the public name."""
    return adb_server_already_running()
