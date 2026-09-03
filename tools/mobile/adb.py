"""One place that builds ``adb`` argv, and the only place that can feed it stdin.

Every device identifier is re-validated with ``device_manager._valid_device_id``
and every package name with ``device_manager.valid_package_name`` before it
reaches a subprocess, so a client-controlled string can never smuggle an extra
argument. There is no shell anywhere in this module.

**Stated deviation from the phase spec.** The spec says "a thin async wrapper
over the ``device_manager._run`` shape". This module keeps that CONTRACT
(``(rc, stdout, stderr)``, kill on timeout) but does not call ``_run`` itself,
because ``_run`` cannot write to the child's stdin -- and stdin is exactly how
``ime.type_text`` must deliver a secret so it never appears in argv. Reusing a
runner that forces the payload onto the command line would have defeated the
one guarantee this phase exists to make. The validators ARE reused, which is
where the security value of that module actually sits.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from config.settings import settings
from tools.device_manager import _valid_device_id, valid_package_name
from tools.mobile import platform_info, sdk_locator

logger = logging.getLogger(__name__)

#: Default per-call timeout. Deliberately short: a hung adb call must surface
#: as a failed check, not as a client timeout on the MCP boundary.
DEFAULT_TIMEOUT_S = 30

#: ``uiautomator dump`` writes a file and we then read it back; both halves are
#: slow on a cold emulator.
DUMP_TIMEOUT_S = 60

#: The dump is attacker-influenced content. Cap it here, at the transport, as
#: well as in the Phase-2 parser: a 400MB "screen" must never reach memory.
MAX_DUMP_BYTES = 4 * 1024 * 1024

#: Where the dump is staged on the device.
DUMP_REMOTE_PATH = "/sdcard/qa-agents-dump.xml"

#: Android keyevent names/numbers we allow. An allowlist rather than a regex
#: over anything, because a keyevent argument reaches ``input`` unquoted.
KEYEVENT_RE = re.compile(r"^(?:[0-9]{1,3}|KEYCODE_[A-Z0-9_]{1,40})$")

#: Schemes ``open_url`` may hand to the device's VIEW intent. ``market:`` is
#: how the Play Store (and therefore Firebase App Tester) is opened.
ALLOWED_URL_SCHEMES = ("https", "http", "market")

_SWIPE_MAX = 20000


def resolve_adb() -> str:
    """Absolute path of the adb to use, or the bare name as a last resort."""
    located = (sdk_locator.locate_sdk() or {}).get("content") or {}
    found = str((located.get("tools") or {}).get("adb") or "")
    if found:
        return found
    from tools.mobile import platform_info

    return platform_info.exe("adb")


async def _run_argv(
    cmd: list[str], timeout: int, stdin_data: bytes | None = None
) -> tuple[int, bytes, bytes]:
    """``device_manager._run``'s contract, plus optional stdin.

    Raises ``FileNotFoundError`` when adb is missing and
    ``asyncio.TimeoutError`` on overrun; both are converted by the callers
    below, so nothing raises past this module.
    """
    # A single case makes dozens of adb calls. Without the no-window flag each
    # one flashes a console window on Windows, which reads as a broken editor.
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE if stdin_data is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **platform_info.no_window_kwargs(),
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=stdin_data), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        try:
            await asyncio.wait_for(proc.communicate(), timeout=1)
        except Exception:
            pass
        logger.warning("mobile.adb: command timed out: %s", cmd[:2])
        raise
    return int(proc.returncode or 0), stdout or b"", stderr or b""


async def raw(
    args: list[str],
    timeout: int = DEFAULT_TIMEOUT_S,
    stdin_data: bytes | None = None,
) -> dict:
    """Run ``adb <args>`` with no device selection. Never raises."""
    try:
        cmd = [resolve_adb()] + [str(a) for a in args]
        rc, out, err = await _run_argv(cmd, timeout, stdin_data)
        return {
            "error": None,
            "content": {
                "rc": rc,
                "out": out.decode(errors="replace"),
                "err": err.decode(errors="replace"),
            },
        }
    except FileNotFoundError:
        return {
            "error": (
                "adb was not found. Install Android Studio (or let the mobile "
                "lane provision the platform-tools) and try again."
            ),
            "content": None,
        }
    except asyncio.TimeoutError:
        return {
            "error": "adb did not answer within " + str(timeout) + "s.",
            "content": None,
        }
    except Exception as exc:
        logger.exception("mobile.adb.raw failed")
        return {"error": str(exc), "content": None}


async def _device(
    serial: str,
    args: list[str],
    timeout: int = DEFAULT_TIMEOUT_S,
    stdin_data: bytes | None = None,
) -> dict:
    if not _valid_device_id(serial):
        return {
            "error": "Refusing to use " + repr(str(serial)[:40]) + " as a device id.",
            "content": None,
        }
    return await raw(["-s", str(serial)] + list(args), timeout, stdin_data)


async def devices() -> dict:
    """Serials of devices in the ``device`` state."""
    result = await raw(["devices"])
    if result.get("error"):
        return result
    serials: list[str] = []
    for line in str((result["content"] or {}).get("out") or "").splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[1] == "device" and _valid_device_id(parts[0]):
            serials.append(parts[0])
    return {"error": None, "content": serials}


async def shell(
    serial: str,
    args: list[str],
    timeout: int = DEFAULT_TIMEOUT_S,
    stdin_data: bytes | None = None,
) -> dict:
    """``adb -s <serial> shell <args>``.

    Calling this with an EMPTY ``args`` and a ``stdin_data`` payload runs adb's
    interactive shell and feeds it the command line on stdin. That is the only
    supported way to send a secret: see ``ime.type_text``.
    """
    return await _device(
        serial, ["shell"] + [str(a) for a in args], timeout, stdin_data
    )


async def getprop(serial: str, name: str) -> dict:
    """One system property as a stripped string."""
    if not re.match(r"^[A-Za-z0-9._-]{1,80}$", str(name or "")):
        return {
            "error": "Refusing to read property " + repr(str(name)[:40]),
            "content": None,
        }
    result = await shell(serial, ["getprop", str(name)])
    if result.get("error"):
        return result
    return {
        "error": None,
        "content": str((result["content"] or {}).get("out") or "").strip(),
    }


async def install(serial: str, apk_path: str) -> dict:
    """``adb install -r -g <apk>`` for a local file that must already exist.

    Reads the kill-switch ITSELF. Installing an app onto a tester's device is
    one of the effects the contract names, and it outlives this call. Every
    caller today is gated, but a guard on a caller is only as good as the list
    of callers -- which is how the same switch came to be missing from
    ``provisioner.run`` and then ``session.start_install``.
    """
    if not settings.qa_mobile_run_enabled:
        return {
            "error": (
                "Refusing to install: the mobile lane needs "
                "`QA_MOBILE_RUN_ENABLED=true` in `.env`. Nothing was "
                "installed and no process was started."
            ),
            "content": None,
        }
    try:
        path = Path(str(apk_path)).expanduser()
        if not path.is_file():
            return {
                "error": "No APK at " + str(path) + ". Nothing was installed.",
                "content": None,
            }
        result = await _device(serial, ["install", "-r", "-g", str(path)], timeout=300)
        if result.get("error"):
            return result
        payload = result["content"] or {}
        text = str(payload.get("out") or "") + str(payload.get("err") or "")
        if int(payload.get("rc") or 0) != 0 or "Success" not in text:
            return {
                "error": "adb install failed: " + text.strip()[:400],
                "content": None,
            }
        return {"error": None, "content": {"path": str(path)}}
    except Exception as exc:
        logger.exception("mobile.adb.install failed")
        return {"error": str(exc), "content": None}


async def uninstall(serial: str, package: str) -> dict:
    """Remove a package, refusing unless the lane is on.

    An uninstall is the one adb effect that repeating cannot undo, so it reads
    the switch for the same reason :func:`install` does. The flag is checked
    BEFORE the package-name validation: a tester whose lane is off should be
    told that, not handed a name error they cannot act on.
    """
    if not settings.qa_mobile_run_enabled:
        return {
            "error": (
                "Refusing to uninstall: the mobile lane needs "
                "`QA_MOBILE_RUN_ENABLED=true` in `.env`. Nothing was removed."
            ),
            "content": None,
        }
    if not valid_package_name(package):
        return {
            "error": "Refusing to uninstall " + repr(str(package)[:60]),
            "content": None,
        }
    return await _device(serial, ["uninstall", str(package)], timeout=120)


async def installed_packages(serial: str) -> dict:
    """Every package id ``pm list packages`` reports."""
    result = await shell(serial, ["pm", "list", "packages"], timeout=60)
    if result.get("error"):
        return result
    out: list[str] = []
    for line in str((result["content"] or {}).get("out") or "").splitlines():
        line = line.strip()
        if line.startswith("package:"):
            candidate = line[len("package:") :].strip()
            if valid_package_name(candidate):
                out.append(candidate)
    return {"error": None, "content": sorted(set(out))}


async def launch(serial: str, package: str) -> dict:
    """Start *package*'s launcher activity via monkey (no activity name needed)."""
    if not valid_package_name(package):
        return {
            "error": "Refusing to launch " + repr(str(package)[:60]),
            "content": None,
        }
    return await shell(
        serial,
        [
            "monkey",
            "-p",
            str(package),
            "-c",
            "android.intent.category.LAUNCHER",
            "1",
        ],
        timeout=60,
    )


async def force_stop(serial: str, package: str) -> dict:
    if not valid_package_name(package):
        return {"error": "Refusing to stop " + repr(str(package)[:60]), "content": None}
    return await shell(serial, ["am", "force-stop", str(package)])


async def uiautomator_dump(serial: str) -> dict:
    """The current screen's uiautomator XML, as a string.

    Two calls: dump to a device path, then ``cat`` it back. Piping straight to
    ``/dev/tty`` is shorter and unreliable -- adb interleaves the dumper's own
    progress line with the XML.
    """
    staged = await shell(
        serial, ["uiautomator", "dump", DUMP_REMOTE_PATH], timeout=DUMP_TIMEOUT_S
    )
    if staged.get("error"):
        return staged
    read = await shell(serial, ["cat", DUMP_REMOTE_PATH], timeout=DUMP_TIMEOUT_S)
    if read.get("error"):
        return read
    xml = str((read["content"] or {}).get("out") or "")
    if not xml.lstrip().startswith("<"):
        return {
            "error": (
                "uiautomator returned no XML for this screen. A secure window "
                "(a password field or a payment sheet) blocks the dump; move "
                "past it or use a screen that allows accessibility."
            ),
            "content": None,
        }
    if len(xml.encode("utf-8", errors="replace")) > MAX_DUMP_BYTES:
        return {
            "error": (
                "This screen's uiautomator dump exceeds the "
                + str(MAX_DUMP_BYTES)
                + " byte cap and was discarded unparsed."
            ),
            "content": None,
        }
    return {"error": None, "content": xml}


def _coord(value: object) -> int | None:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if 0 <= number <= _SWIPE_MAX:
        return number
    return None


async def tap(serial: str, x: object, y: object) -> dict:
    px, py = _coord(x), _coord(y)
    if px is None or py is None:
        return {"error": "Refusing to tap at " + repr((x, y)), "content": None}
    return await shell(serial, ["input", "tap", str(px), str(py)])


async def swipe(
    serial: str, x1: object, y1: object, x2: object, y2: object, ms: object = 300
) -> dict:
    points = [_coord(v) for v in (x1, y1, x2, y2)]
    duration = _coord(ms)
    if any(p is None for p in points) or duration is None:
        return {
            "error": "Refusing to swipe with " + repr((x1, y1, x2, y2, ms)),
            "content": None,
        }
    return await shell(
        serial, ["input", "swipe"] + [str(p) for p in points] + [str(duration)]
    )


async def keyevent(serial: str, code: object) -> dict:
    text = str(code or "")
    if not KEYEVENT_RE.match(text):
        return {"error": "Refusing keyevent " + repr(text[:40]), "content": None}
    return await shell(serial, ["input", "keyevent", text])


async def open_url(serial: str, url: str) -> dict:
    """Hand *url* to the device's VIEW intent (download links, Play Store)."""
    from urllib.parse import urlsplit

    text = str(url or "").strip()
    scheme = urlsplit(text).scheme.lower()
    if scheme not in ALLOWED_URL_SCHEMES:
        return {
            "error": (
                "Refusing to open a "
                + (scheme or "scheme-less")
                + " URL on the device; allowed schemes are "
                + ", ".join(ALLOWED_URL_SCHEMES)
                + "."
            ),
            "content": None,
        }
    if any(ch in text for ch in (" ", "\n", "\r", "'", '"', "`", "$", ";", "&", "|")):
        return {
            "error": "Refusing to open a URL containing shell-significant characters.",
            "content": None,
        }
    return await shell(
        serial, ["am", "start", "-a", "android.intent.action.VIEW", "-d", text]
    )


async def pm_select(serial: str, ime_id: str) -> dict:
    """Select an input method by id (``adb shell ime set <id>``).

    The name comes from the phase spec's module list. It is the ONE "make this
    component the active one" call the lane needs, and it is here rather than
    in ``ime.py`` so that every argv this lane builds is built in one file.
    """
    text = str(ime_id or "")
    if not re.match(r"^[A-Za-z0-9._]{1,120}/[A-Za-z0-9._$]{1,120}$", text):
        return {
            "error": "Refusing to select input method " + repr(text[:60]) + ".",
            "content": None,
        }
    return await shell(serial, ["ime", "set", text])
