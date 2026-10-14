"""The change side of avd_manage: ``boot``, ``create`` and ``delete``."""

from __future__ import annotations

from tools.mobile import emulator
from tools.mobile.avd_manage_args import _GET_AN_IMAGE, _NO_AVDMANAGER, _WARN
from tools.mobile.avd_manage_host import host
from tools.mobile.avd_manage_images import (
    _accel_gate,
    _avd_names,
    _avdmanager,
    _names_line,
    _tools,
)
from tools.untrusted import single_line, wrap_untrusted


def _list_error(verb: str, err: str) -> str:
    return (
        _WARN
        + "Could not list the AVDs, so nothing was %s.\n" % verb
        + wrap_untrusted("emulator -list-avds", err)
    )


async def _unknown_avd(name: str, verb: str) -> str:
    """The refusal when the AVD list fails or lacks *name*, else ``""``."""
    names, err = await _avd_names()
    if err:
        return _list_error(verb, err)
    if name not in names:
        return (
            _WARN
            + "No AVD with that name exists, so nothing was %s. Existing: " % verb
            + (_names_line(names) or "none")
        )
    return ""


async def _boot_ready(name: str) -> str:
    """``""`` when *name* exists and is not running; else the reply to return."""
    refused = await _unknown_avd(name, "started")
    if refused:
        return refused
    live = await emulator.find_running(name)
    if live.get("error"):
        return (
            _WARN
            + "Could not tell whether that AVD is already running, so nothing was started.\n"
            + wrap_untrusted("adb", str(live["error"]))
        )
    serial = str((live.get("content") or {}).get("serial") or "")
    if serial:
        return "`%s` is already running as `%s`. Pass `device_id=%s`." % (
            single_line(name, 80),
            single_line(serial, 40),
            single_line(serial, 40),
        )
    return ""


async def _boot(name: str) -> str:
    refused = await _boot_ready(name)
    if refused:
        return refused
    blocked = await _accel_gate()
    if blocked:
        return blocked
    started = await emulator.start(name)
    if started.get("error"):
        return (
            _WARN
            + "The emulator did not start.\n"
            + wrap_untrusted("emulator", str(started["error"]))
        )
    return (
        "Started `%s`. It needs a minute to boot: call `qa_list_devices`, and "
        "when it shows up as a device, continue with `device_id`."
        % single_line(name, 80)
    )


async def _create_name_refusal(name: str) -> str:
    """The refusal for a ``create`` whose name cannot be used, or ``""``."""
    names, err = await _avd_names()
    if err:
        return _list_error("created", err)
    if name in names:
        return (
            _WARN
            + "An AVD with that name already exists; this server never overwrites one. "
            "Pick another name, or boot it with `emulator=boot`."
        )
    return ""


def _image_refusal(system_image: str, sdk_root: str) -> str:
    """The refusal for a ``system_image`` that is not installed, or ``""``."""
    images = host().installed_images(sdk_root)
    if not images:
        return _WARN + _GET_AN_IMAGE
    if system_image not in images:
        return (
            _WARN
            + "`system_image` must be one of the installed images: "
            + ", ".join("`%s`" % image for image in images[: host().MAX_IMAGES_LISTED])
            + ". Nothing was created."
        )
    return ""


def _created(name: str, system_image: str) -> str:
    return (
        "Created the AVD `%s` from `%s`. Start it with `qa_mobile_test` and "
        "`emulator=boot`, `avd=%s`, `apply=true`."
        % (single_line(name, 80), single_line(system_image, 120), single_line(name, 80))
    )


async def _create(name: str, system_image: str) -> str:
    blocked = await _accel_gate()
    if blocked:
        return blocked
    refused = await _create_name_refusal(name)
    if refused:
        return refused
    sdk_root, binary = _tools()
    refused = _image_refusal(system_image, sdk_root)
    if refused:
        return refused
    if not binary:
        return _WARN + _NO_AVDMANAGER
    return await _run_create(binary, name, system_image)


async def _run_create(binary: str, name: str, system_image: str) -> str:
    # "no" answers avdmanager's hardware-profile prompt; the pipe also keeps it
    # from ever reading this process's own stdin (the MCP transport).
    rc, text = await _avdmanager(
        binary, ["create", "avd", "-n", name, "-k", system_image], stdin_text="no\n"
    )
    if rc != 0:
        return (
            _WARN
            + "avdmanager could not create the AVD.\n"
            + wrap_untrusted("avdmanager", text)
        )
    return _created(name, system_image)


async def _delete_guard(name: str) -> str:
    """``""`` when *name* exists, is not running and was not just started."""
    refused = await _unknown_avd(name, "deleted")
    if refused:
        return refused
    live = await emulator.find_running(name)
    if live.get("error"):
        return (
            _WARN
            + "Could not tell whether that AVD is running, so nothing was deleted.\n"
            + wrap_untrusted("adb", str(live["error"]))
        )
    if (live.get("content") or {}).get("serial"):
        return (
            _WARN
            + "That AVD is running and will not be deleted while it runs. Close it first."
        )
    return host()._started_refusal(name)


async def _delete(name: str) -> str:
    refused = await _delete_guard(name)
    if refused:
        return refused
    _root, binary = _tools()
    if not binary:
        return _WARN + _NO_AVDMANAGER
    rc, text = await _avdmanager(binary, ["delete", "avd", "-n", name])
    if rc != 0:
        return (
            _WARN
            + "avdmanager could not delete the AVD.\n"
            + wrap_untrusted("avdmanager", text)
        )
    return "Deleted the AVD `%s`. Nothing else was touched." % single_line(name, 80)
