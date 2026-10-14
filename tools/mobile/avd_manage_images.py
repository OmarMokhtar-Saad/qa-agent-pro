"""The read side of avd_manage: tool lookup, AVD names, the acceleration gate, ``list``."""

from __future__ import annotations

import asyncio

from tools.mobile import emulator, platform_info, sdk_locator
from tools.mobile.avd_manage_args import (
    _GET_AN_IMAGE,
    _ODD_IMAGES,
    _REAL_DEVICE,
    _WARN,
)
from tools.mobile.avd_manage_host import host
from tools.untrusted import single_line, wrap_untrusted


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
    shown = [single_line(name, 80) for name in names[: host().MAX_AVDS_LISTED]]
    line = ", ".join("`%s`" % name for name in shown)
    more = len(names) - len(shown)
    return line + (" (and %d more)" % more if more > 0 else "")


def _accel_warning(content: dict) -> str:
    """The EXPLAIN-ONLY text for a host that cannot accelerate the emulator."""
    fix = str(
        content.get("fix") or emulator.accel_fix(str(content.get("output") or ""))
    )
    return (
        _WARN + "This computer cannot run the Android emulator with hardware "
        "acceleration, so nothing was started or created. " + fix + " " + _REAL_DEVICE
    )


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
        return _accel_warning(content)
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
        platform_info.run_sync,
        [binary, *args],
        host().AVDMANAGER_TIMEOUT_S,
        stdin_text=stdin_text,
    )
    return rc, (err.strip() or out.strip() or "rc=%d" % rc)


async def _live_serials() -> dict[str, str]:
    """AVD name -> serial for every emulator that is running now."""
    live: dict[str, str] = {}
    running = await emulator.list_running()
    if not running.get("error"):
        for item in running.get("content") or []:
            live[str(item.get("avd") or "")] = str(item.get("serial") or "")
    return live


async def _avds_block(names: list[str], err: str) -> list[str]:
    cap = host().MAX_AVDS_LISTED
    if err:
        return [
            _WARN + "Could not list the AVDs.",
            wrap_untrusted("emulator -list-avds", err),
        ]
    if not names:
        return ["No AVD exists yet."]
    live = await _live_serials()
    lines = []
    for name in names[:cap]:
        tag = " (running as `%s`)" % single_line(live[name], 40) if name in live else ""
        lines.append("- `%s`%s" % (single_line(name, 80), tag))
    if len(names) > cap:
        lines.append("- ... and %d more" % (len(names) - cap))
    return lines


def _images_block(sdk_root: str) -> list[str]:
    cap = host().MAX_IMAGES_LISTED
    images, odd = host()._scan_images(sdk_root)
    lines = ["", "Installed system images (the only ones `emulator=create` can use):"]
    if images:
        lines += ["- `%s`" % image for image in images[:cap]]
        if len(images) > cap:
            lines.append("- ... and %d more" % (len(images) - cap))
    else:
        lines.append(_GET_AN_IMAGE)
    if odd:
        lines.append(_ODD_IMAGES % odd)
    return lines


async def _list() -> str:
    names, err = await _avd_names()
    sdk_root, _binary = _tools()
    lines = ["## Android virtual devices", ""]
    lines += await _avds_block(names, err)
    lines += _images_block(sdk_root)
    lines += [
        "",
        "Boot: `emulator=boot`, `avd=NAME`, `apply=true`. Create: `emulator=create`, "
        "`avd=NAME`, `system_image=<one of the ids above>`, `apply=true`. Delete: "
        "`emulator=delete`, `avd=NAME`, `apply=true` and `confirm_destructive=true` "
        "(only after the tester says yes).",
    ]
    return "\n".join(lines)
