"""List, boot, create and delete Android virtual devices (AVDs) for the mobile lane.

``qa_mobile_test(emulator=list|boot|create|delete)`` ends here. A model that was
told "there is no AVD" used to run ``avdmanager`` itself through a raw shell and
skip the destructive guard; this module is the one governed path for that work.

The rules, in the order they are checked:

* an AVD name must pass ``sdk_locator.avd_name_is_safe``, else the call is
  REFUSED BY NAME and nothing runs;
* ``delete`` needs ``confirm_destructive`` to be exactly ``True`` (the tester's
  yes) AND ``apply=true``; it removes ONE AVD that exists and is not running --
  the only argv it ever builds is ``delete avd -n NAME``;
* ``boot`` and ``create`` need ``apply=true`` and run ``emulator -accel-check``
  FIRST. A host without a working hypervisor is only EXPLAINED (the fix text
  from ``platform_info`` and "use a real device"): no driver is installed and
  nothing is started or created;
* ``create`` uses a system image that is ALREADY installed under
  ``<sdk>/system-images``. It never downloads one (``sdkmanager`` is never
  run) and never passes ``--force``, so an existing AVD is never overwritten;
* ``boot`` is ``emulator.start`` -- the one spawn site -- after ``find_running``,
  so an AVD that is already running is reused, not started twice.

``list`` is read-only. Text a tool printed goes through ``wrap_untrusted``; AVD
names are device-side text and go through ``single_line``. Every subprocess is
``platform_info._run_sync`` on a worker thread with a timeout. Never raises.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from tools.mobile import emulator, platform_info, sdk_locator
from tools.untrusted import single_line, wrap_untrusted

logger = logging.getLogger(__name__)

#: Seconds one ``avdmanager`` call may run. It is a JVM start plus a few small
#: file writes; the long case is a cold disk. A tester waits inside an MCP
#: reply, so a raise here only lengthens a hang. Bounded from above in
#: ``tests/mobile/test_mobile_bounds_upper.py``.
AVDMANAGER_TIMEOUT_S = 120

#: Installed system images shown in one reply. Display only: the membership
#: check for ``create`` reads the FULL list.
MAX_IMAGES_LISTED = 40

#: AVDs shown in one reply. Display only.
MAX_AVDS_LISTED = 40

ACTIONS = ("list", "boot", "create", "delete")

_WARN = "\u26a0\ufe0f "
_REAL_DEVICE = (
    "A real Android device needs no emulator: connect one with USB debugging "
    "on, then call `qa_list_devices`."
)
_GET_AN_IMAGE = (
    "No system image is installed, and this server downloads none. Install one "
    "yourself: Android Studio > Tools > SDK Manager > SDK Platforms, tick "
    "'Show Package Details' and pick a 'Google APIs' or 'Google Play' system "
    "image that matches this computer's CPU (arm64 on Apple silicon, x86_64 "
    "otherwise). Then call this again."
)
_ODD_IMAGES = (
    "%d system-image folder(s) skipped: a folder name with a character other "
    "than letters, digits, `.`, `_`, `-` cannot be used here. Android Studio "
    "can still use it."
)
_NO_AVDMANAGER = (
    "`avdmanager` was not found in the Android SDK (it ships with the 'Android "
    "SDK Command-line Tools'). Install them in Android Studio > SDK Manager > "
    "SDK Tools, then call this again."
)


def _part_ok(part: str) -> bool:
    """One folder name of an image id: ASCII letters, digits, ``.``, ``_``, ``-``."""
    return bool(part) and all(c.isascii() and (c.isalnum() or c in "._-") for c in part)


def _entries(folder: Path) -> list[Path]:
    """What ``folder`` holds, sorted; ``[]`` when it cannot be listed. A plain file
    among the image folders (a ``.DS_Store``) must not end the whole scan."""
    try:
        return sorted(folder.iterdir())
    except OSError:
        return []


def _scan_images(sdk_root: str) -> tuple[list[str], int]:
    """``(image ids, image folders skipped for an odd name)``.

    An image is ``<sdk>/system-images/<api>/<tag>/<abi>/`` holding a
    ``source.properties`` or ``package.xml``. Reads directory names only.
    A folder whose api, tag or abi name fails ``_part_ok`` is counted, not
    returned: the id is shown in a reply and goes into the avdmanager argv,
    which on Windows passes through ``cmd``.
    """
    found: list[str] = []
    skipped = 0
    if not sdk_root:
        return found, skipped
    try:
        base = Path(sdk_root) / "system-images"
        for api in _entries(base):
            for tag in _entries(api):
                for abi in _entries(tag):
                    if not (
                        (abi / "source.properties").is_file()
                        or (abi / "package.xml").is_file()
                    ):
                        continue
                    if not all(_part_ok(p.name) for p in (api, tag, abi)):
                        skipped += 1
                        continue
                    found.append(
                        ";".join(("system-images", api.name, tag.name, abi.name))
                    )
    except OSError:
        pass
    return found, skipped


def installed_images(sdk_root: str) -> list[str]:
    """Package ids of the system images already on disk, e.g.
    ``system-images;android-34;google_apis;x86_64``. ``[]`` when there are none.
    An oddly named folder is never one of them (see ``_scan_images``).
    """
    return _scan_images(sdk_root)[0]


def _tools() -> tuple[str, str]:
    """``(sdk_root, avdmanager path)``; either may be ``""``."""
    located = (sdk_locator.locate_sdk() or {}).get("content") or {}
    return (
        str(located.get("sdk_root") or ""),
        str((located.get("tools") or {}).get("avdmanager") or ""),
    )


async def _avd_names() -> tuple[list[str], str]:
    """``(names, error)``; *error* is ``""`` on success."""
    listed = await emulator.list_avds()
    if listed.get("error"):
        return [], str(listed["error"])
    return [str(name) for name in listed.get("content") or []], ""


def _names_line(names: list[str]) -> str:
    shown = [single_line(name, 80) for name in names[:MAX_AVDS_LISTED]]
    line = ", ".join("`%s`" % name for name in shown)
    more = len(names) - len(shown)
    return line + (" (and %d more)" % more if more > 0 else "")


async def _accel_gate() -> str:
    """``""`` when the emulator may be used; else the explanation to return.

    ``ok`` of ``None`` (the check timed out) is not a verdict, so it does not
    block: the emulator reports its own failure. ``False`` is EXPLAIN-ONLY.
    """
    checked = await emulator.accel_check()
    if checked.get("error"):
        return (
            _WARN
            + "Could not check hardware acceleration, so nothing was started or created.\n"
            + wrap_untrusted("emulator -accel-check", str(checked["error"]))
        )
    content = checked.get("content") or {}
    if content.get("ok") is False:
        fix = str(
            content.get("fix") or emulator.accel_fix(str(content.get("output") or ""))
        )
        return (
            _WARN + "This computer cannot run the Android emulator with hardware "
            "acceleration, so nothing was started or created. "
            + fix
            + " "
            + _REAL_DEVICE
        )
    return ""


async def _avdmanager(
    binary: str, args: list[str], stdin_text: str = ""
) -> tuple[int, str]:
    """``(rc, text)`` of one bounded ``avdmanager`` call on a worker thread.

    *stdin_text* is never ``None``: every call gets its own stdin pipe, closed
    after the text, so ``avdmanager`` can never read this process's stdin (the
    MCP transport). ``""`` is plain EOF.
    """
    rc, out, err = await asyncio.to_thread(
        platform_info._run_sync,
        [binary, *args],
        AVDMANAGER_TIMEOUT_S,
        stdin_text=stdin_text,
    )
    return rc, (err.strip() or out.strip() or "rc=%d" % rc)


async def _list() -> str:
    names, err = await _avd_names()
    sdk_root, _binary = _tools()
    lines = ["## Android virtual devices", ""]
    if err:
        lines += [
            _WARN + "Could not list the AVDs.",
            wrap_untrusted("emulator -list-avds", err),
        ]
    elif names:
        live: dict[str, str] = {}
        running = await emulator.list_running()
        if not running.get("error"):
            for item in running.get("content") or []:
                live[str(item.get("avd") or "")] = str(item.get("serial") or "")
        for name in names[:MAX_AVDS_LISTED]:
            tag = (
                " (running as `%s`)" % single_line(live[name], 40)
                if name in live
                else ""
            )
            lines.append("- `%s`%s" % (single_line(name, 80), tag))
        if len(names) > MAX_AVDS_LISTED:
            lines.append("- ... and %d more" % (len(names) - MAX_AVDS_LISTED))
    else:
        lines.append("No AVD exists yet.")
    images, odd = _scan_images(sdk_root)
    lines += ["", "Installed system images (the only ones `emulator=create` can use):"]
    if images:
        lines += ["- `%s`" % image for image in images[:MAX_IMAGES_LISTED]]
        if len(images) > MAX_IMAGES_LISTED:
            lines.append("- ... and %d more" % (len(images) - MAX_IMAGES_LISTED))
    else:
        lines.append(_GET_AN_IMAGE)
    if odd:
        lines.append(_ODD_IMAGES % odd)
    lines += [
        "",
        "Boot: `emulator=boot`, `avd=NAME`, `apply=true`. Create: `emulator=create`, "
        "`avd=NAME`, `system_image=<one of the ids above>`, `apply=true`. Delete: "
        "`emulator=delete`, `avd=NAME`, `apply=true` and `confirm_destructive=true` "
        "(only after the tester says yes).",
    ]
    return "\n".join(lines)


async def _boot(name: str) -> str:
    names, err = await _avd_names()
    if err:
        return (
            _WARN
            + "Could not list the AVDs, so nothing was started.\n"
            + wrap_untrusted("emulator -list-avds", err)
        )
    if name not in names:
        return (
            _WARN
            + "No AVD with that name exists, so nothing was started. Existing: "
            + (_names_line(names) or "none")
        )
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


async def _create(name: str, system_image: str) -> str:
    blocked = await _accel_gate()
    if blocked:
        return blocked
    names, err = await _avd_names()
    if err:
        return (
            _WARN
            + "Could not list the AVDs, so nothing was created.\n"
            + wrap_untrusted("emulator -list-avds", err)
        )
    if name in names:
        return (
            _WARN
            + "An AVD with that name already exists; this server never overwrites one. "
            "Pick another name, or boot it with `emulator=boot`."
        )
    sdk_root, binary = _tools()
    images = installed_images(sdk_root)
    if not images:
        return _WARN + _GET_AN_IMAGE
    if system_image not in images:
        return (
            _WARN
            + "`system_image` must be one of the installed images: "
            + ", ".join("`%s`" % image for image in images[:MAX_IMAGES_LISTED])
            + ". Nothing was created."
        )
    if not binary:
        return _WARN + _NO_AVDMANAGER
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
    return (
        "Created the AVD `%s` from `%s`. Start it with `qa_mobile_test` and "
        "`emulator=boot`, `avd=%s`, `apply=true`."
        % (single_line(name, 80), single_line(system_image, 120), single_line(name, 80))
    )


async def _delete(name: str) -> str:
    names, err = await _avd_names()
    if err:
        return (
            _WARN
            + "Could not list the AVDs, so nothing was deleted.\n"
            + wrap_untrusted("emulator -list-avds", err)
        )
    if name not in names:
        return (
            _WARN
            + "No AVD with that name exists, so nothing was deleted. Existing: "
            + (_names_line(names) or "none")
        )
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
    # adb lists an emulator only some seconds after it is spawned, so one this
    # server just started is not in `live` yet.
    if any(str(row.get("avd") or "") == name for row in emulator.recently_started()):
        return (
            _WARN
            + "That AVD was started moments ago and may still be booting, so it was "
            "not deleted. Close it first, or try again once it has stopped."
        )
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


def _refusal(what: str, name: str, apply: bool, confirm_destructive: bool) -> str:
    """The refusal markdown for a change action, or ``""`` when it may run."""
    if not name:
        return _WARN + "`emulator=%s` needs `avd=<name>`." % what
    # A leading dash would reach avdmanager as a flag (`-n --force`). ASCII only,
    # like the image ids; Android Studio is believed to build no other AVD id.
    if (
        name.startswith("-")
        or not name.isascii()
        or not sdk_locator.avd_name_is_safe(name)
    ):
        return (
            _WARN
            + "That is not a safe AVD name (ASCII letters, digits, `.`, `_` and `-`; "
            "no leading dot or dash). Nothing was run."
        )
    if what == "delete" and confirm_destructive is not True:
        return (
            _WARN + "Deleting an AVD is permanent. Ask the tester; only after they say "
            "yes call again with `confirm_destructive=true`. Nothing was deleted."
        )
    if apply is not True:
        return (
            _WARN + "`emulator=%s` changes this computer, so it needs `apply=true`. "
            "Nothing was run." % what
        )
    return ""


async def manage(
    action: str,
    *,
    avd: str = "",
    system_image: str = "",
    apply: bool = False,
    confirm_destructive: bool = False,
) -> str:
    """Run one AVD action and return markdown. Never raises.

    Refusals come before anything runs, in this order: unknown action, missing
    or unsafe name, ``delete`` without ``confirm_destructive is True``, any
    change without ``apply is True``. *confirm_destructive* and *apply* must be
    the literal ``True``: a truthy string does not count.
    """
    try:
        what = str(action or "").strip().lower()
        if what == "list":
            return await _list()
        if what not in ACTIONS:
            return (
                _WARN
                + "Unknown emulator action. Use one of: list, boot, create, delete."
            )
        name = str(avd or "").strip()
        refusal = _refusal(what, name, apply, confirm_destructive)
        if refusal:
            return refusal
        if what == "delete":
            return await _delete(name)
        if what == "boot":
            return await _boot(name)
        return await _create(name, str(system_image or "").strip())
    except Exception:
        logger.exception("mobile.avd_manage failed")
        return (
            _WARN
            + "The emulator action failed unexpectedly; nothing further was run. See the server log."
        )
