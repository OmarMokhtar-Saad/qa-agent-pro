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
import time
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
    # A NON-ZERO EXIT IS A FAILED PROBE, NOT AN EMPTY MACHINE. `raw` only sets
    # `error` when adb could not be run at all (spawn failure, timeout); a
    # command that ran and failed arrives here with error=None and rc!=0, and
    # its stdout carries no serials. Reporting that as an empty list told the
    # caller "nothing is booted", which is how a broken adb server ended up
    # spawning a second emulator over the tester's own (D1, 2026-09-03).
    body = result.get("content") or {}
    rc = int(body.get("rc") or 0)
    if rc != 0:
        detail = (
            str(body.get("err") or "").strip() or str(body.get("out") or "").strip()
        )
        return {
            "error": "adb devices failed ("
            + (detail[:200] if detail else "exit " + str(rc))
            + ").",
            "content": None,
        }
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


async def current_activity(serial: str) -> dict:
    """The focused activity as ``package/Activity``, or "" when unknowable.

    The uiautomator dump does NOT carry the activity, so `perception` took it as
    a parameter and every caller passed the empty default -- the value was
    threaded end to end with no producer anywhere. `screen_id` therefore hashed
    the package and the top three texts only, and two screens of one app with
    similar wording could share an identity, which the report dedupes on and
    `assert screen_changed` compares.

    ``mCurrentFocus`` is read rather than ``dumpsys activity activities``: it is
    one short line, it names the window that actually has focus (which is what a
    tap will hit), and it does not depend on the activity-stack format, which
    differs across Android versions.

    An empty answer is a NORMAL result, not an error: a dialog or a system
    window can leave no resolvable component, and a screen identity that is
    weaker than it could be is far better than a refused dump.
    """
    result = await shell(serial, ["dumpsys", "window"], timeout=20)
    if result.get("error"):
        return {"error": None, "content": ""}
    text = str((result["content"] or {}).get("out") or "")
    for line in text.splitlines():
        if "mCurrentFocus" not in line:
            continue
        found = re.search(r"([A-Za-z0-9_.]+/[A-Za-z0-9_.$]+)", line)
        if found:
            return {"error": None, "content": found.group(1)[:200]}
    return {"error": None, "content": ""}


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


# The digit guards are load-bearing: without them `\d{1,7}` happily matches a
# SEVEN-digit slice of an eight-digit number, so a device reporting a size
# too large to be representable would be read as a plausible one instead of
# refused. Bounded on both sides, an over-long run matches nothing. The
# lookbehind also refuses a leading minus: `-1080x2400` used to match from the
# digit after the sign and be read as a positive 1080 (round-13 review), when a
# negative width is an answer this function must not trust.
_DISPLAY_RE = re.compile(r"(?<![\d-])(\d{1,7})x(\d{1,7})(?!\d)")
#: How long a display answer is reused before the device is asked again. Not
#: forever: a SERIAL is not a device identity. Emulator serials are recycled, so
#: a phone AVD and a tablet AVD both arrive as `emulator-5554`; a foldable
#: changes size mid-run; `wm size WxH` can be set mid-session. Caching for the
#: life of the process made each of those a stale answer that no later call
#: could correct. The window is short enough that a device swap self-heals and
#: long enough that a burst of actions costs one round trip.
DISPLAY_CACHE_TTL_S = 60
#: Per serial: `(monotonic deadline, size or None)`. A FAILED answer is cached
#: too -- a `wm` hiccup must not cost a round trip on every action -- but only
#: for the same window, so it retries rather than degrading for the whole run.
_DISPLAY_CACHE: dict = {}


async def display_size(serial: str) -> dict:
    """The device's NATURAL display size as ``[w, h]``, or ``None`` content.

    ``wm size`` prints ``Physical size: 1080x2400`` and, where the display has
    been resized, an additional ``Override size:`` line. The override is what is
    actually being drawn to, so it wins where both appear.

    This exists because **the display rectangle is not in a uiautomator dump**.
    Four rounds of `perception._select_viewport` tried to infer it from window
    geometry and each fixed the previous round's fix; docs/DECISIONS.md ->
    *The display size is not in the dump* has the measured record. It is a
    device fact, so it is read from the device.

    Never raises and never refuses loudly: a device that cannot answer gets
    ``{"error": None, "content": None}``, and `prune` falls back to a frame
    derived from the dump's own windows. A missing display size must degrade the
    report's scale, never lose the tester's screen.
    """
    key = str(serial or "")
    now = time.monotonic()
    cached = _DISPLAY_CACHE.get(key)
    if cached is not None and now < cached[0]:
        return {"error": None, "content": cached[1]}
    result = await shell(key, ["wm", "size"])
    size = None
    overridden = False
    if not result.get("error"):
        text = str((result.get("content") or {}).get("out") or "")
        for line in text.splitlines():
            lowered = line.strip().lower()
            override = lowered.startswith("override size:")
            if not override and not lowered.startswith("physical size:"):
                continue
            match = _DISPLAY_RE.search(lowered)
            if not match:
                continue
            width, height = int(match.group(1)), int(match.group(2))
            if width <= 0 or height <= 0:
                continue
            if overridden and not override:
                # An override REPLACES the physical size and is what is being
                # drawn to, so it wins on its own merit rather than by being
                # printed second. AOSP prints physical first, but "the last line
                # wins" would silently take the wrong one on a build that does
                # not -- and a report scaled to the panel while the device draws
                # an override is at the wrong scale for the whole run.
                continue
            size = [width, height]
            overridden = overridden or override
    _DISPLAY_CACHE[key] = (now + DISPLAY_CACHE_TTL_S, size)
    return {"error": None, "content": size}


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
    except (TypeError, ValueError, OverflowError):
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


# ── app-log evidence transport (plan mobile-app-evidence, P2) ───────────────
#
# Every function below is TRANSPORT, gated by the lifecycle function that calls
# it (`tools/mobile_evidence/capture.py` reads both flags before any of these),
# in the same position as `launch` and `uiautomator_dump`. Two of them are named
# in tests/mobile/test_mobile_killswitch_surface.py's EFFECT_CALLS so a FUTURE
# public function in this package that calls them directly is seen by the scan.

#: An on-device path this module will read for the run's own package: absolute,
#: a bounded charset, and no `..` segment. A profile or a listing can only ever
#: hand this module something that matches.
_DEVICE_PATH_RE = re.compile(r"^/[A-Za-z0-9_./-]{1,200}$")

#: A logcat tag as `-s` accepts it.
_LOGCAT_TAG_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")

_EPOCH_MS_RE = re.compile(r"^\d{13}$")


def _valid_device_path(path: object) -> bool:
    text = str(path or "")
    return bool(_DEVICE_PATH_RE.match(text)) and ".." not in text.split("/")


def _capped(text: str, max_bytes: int) -> tuple[str, bool]:
    """*text* cut to at most *max_bytes* UTF-8 bytes, and whether it was cut."""
    raw = text.encode("utf-8", errors="replace")
    limit = max(0, int(max_bytes))
    if len(raw) <= limit:
        return text, False
    return raw[:limit].decode("utf-8", errors="replace"), True


async def logcat_clear(serial: str) -> dict:
    """``adb -s <serial> logcat -c``: empty the ring buffer before a case starts."""
    return await _device(serial, ["logcat", "-c"])


async def logcat_dump(
    serial: str,
    *,
    tag: str = "",
    pid: int | None = None,
    max_bytes: int = MAX_DUMP_BYTES,
) -> dict:
    """``logcat -d -v threadtime``, narrowed to *tag* and/or *pid*, byte-capped.

    ``{"error", "content": {"text", "truncated"}}``. The text is never logged: a
    logcat slice is the app's own output and may carry anything the app printed.
    """
    args = ["logcat", "-d", "-v", "threadtime"]
    if pid is not None:
        try:
            number = int(pid)
        except (TypeError, ValueError, OverflowError):
            return {"error": "Refusing logcat pid " + repr(pid)[:40], "content": None}
        if number <= 0:
            return {"error": "Refusing logcat pid " + repr(pid)[:40], "content": None}
        args.append("--pid=" + str(number))
    if tag:
        if not _LOGCAT_TAG_RE.match(str(tag)):
            return {
                "error": "Refusing logcat tag " + repr(str(tag)[:40]),
                "content": None,
            }
        args += ["-s", str(tag)]
    result = await _device(serial, args, timeout=DUMP_TIMEOUT_S)
    if result.get("error"):
        return result
    text, truncated = _capped(
        str((result["content"] or {}).get("out") or ""), max_bytes
    )
    return {"error": None, "content": {"text": text, "truncated": truncated}}


async def pidof(serial: str, package: str) -> int | None:
    """The app's pid, or None when it is not running or the name is refused."""
    if not valid_package_name(package):
        return None
    result = await shell(serial, ["pidof", "-s", str(package)])
    if result.get("error"):
        return None
    out = str((result["content"] or {}).get("out") or "").strip().split()
    if not out or not out[0].isdigit():
        return None
    number = int(out[0])
    return number if number > 0 else None


async def device_epoch_ms(serial: str) -> int | None:
    """The device's wall clock as epoch milliseconds, or None.

    ``date +%s%3N`` on a toybox that lacks ``%N`` prints the literal, which is
    not thirteen digits and therefore reads as None -- never a guessed zero.
    """
    result = await shell(serial, ["date", "+%s%3N"])
    if result.get("error"):
        return None
    text = str((result["content"] or {}).get("out") or "").strip()
    return int(text) if _EPOCH_MS_RE.match(text) else None


def _run_as_refused(rc: int, out: str, err: str) -> str | None:
    text = (out + "\n" + err).strip()
    if rc != 0 or "run-as:" in text.lower():
        return text[:300] or ("run-as exited " + str(rc))
    return None


async def run_as_ls(serial: str, package: str, path: str) -> dict:
    """``run-as <package> ls -1 <path>``: the entry names, or the refusal as error.

    Only the run's own *package* may be named; *path* must be absolute with no
    ``..``. A release (non-debuggable) build makes ``run-as`` refuse, and that
    refusal is the error text so the caller can state it.
    """
    if not valid_package_name(package):
        return {
            "error": "Refusing run-as for " + repr(str(package)[:60]),
            "content": None,
        }
    if not _valid_device_path(path):
        return {
            "error": "Refusing device path " + repr(str(path)[:80]),
            "content": None,
        }
    result = await shell(serial, ["run-as", str(package), "ls", "-1", str(path)])
    if result.get("error"):
        return result
    body = result["content"] or {}
    refused = _run_as_refused(
        int(body.get("rc") or 0), str(body.get("out") or ""), str(body.get("err") or "")
    )
    if refused is not None:
        return {"error": refused, "content": None}
    names = [
        line.strip().rsplit("/", 1)[-1]
        for line in str(body.get("out") or "").splitlines()
        if line.strip()
    ]
    return {"error": None, "content": names}


async def run_as_cat(
    serial: str, package: str, path: str, max_bytes: int = MAX_DUMP_BYTES
) -> dict:
    """``exec-out run-as <package> cat <path>``, byte-capped.

    ``exec-out`` rather than ``shell`` because ``shell`` is a pty and mangles line
    endings. ``{"error", "content": {"data", "truncated"}}``; the data is never
    logged.
    """
    if not valid_package_name(package):
        return {
            "error": "Refusing run-as for " + repr(str(package)[:60]),
            "content": None,
        }
    if not _valid_device_path(path):
        return {
            "error": "Refusing device path " + repr(str(path)[:80]),
            "content": None,
        }
    result = await _device(
        serial,
        ["exec-out", "run-as", str(package), "cat", str(path)],
        timeout=DUMP_TIMEOUT_S,
    )
    if result.get("error"):
        return result
    body = result["content"] or {}
    refused = _run_as_refused(
        int(body.get("rc") or 0), str(body.get("out") or ""), str(body.get("err") or "")
    )
    if refused is not None:
        return {"error": refused, "content": None}
    data, truncated = _capped(str(body.get("out") or ""), max_bytes)
    return {"error": None, "content": {"data": data, "truncated": truncated}}
