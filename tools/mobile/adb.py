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
from tools.device_manager import (
    _valid_device_id,
    parse_adb_devices,
    valid_package_name,
)
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

#: The PNG one screen capture may occupy. TWICE ``MAX_DUMP_BYTES``, because a
#: lossless full-resolution screen is bulkier than the XML that describes it,
#: and the constraint is a different one: this payload is base64-encoded into a single
#: MCP reply the client must hold in one message, so its real cost is ~4/3 of
#: this number, and it is paid on EVERY turn of a run rather than once. A cap
#: here is also the only thing between a device that answers `screencap` with
#: something enormous and this process's memory.
MAX_SCREENSHOT_BYTES = 8 * 1024 * 1024

#: PNG's own eight-byte signature. ``exec-out screencap -p`` writes the image to
#: STDOUT, so a device that refused instead writes TEXT there -- and the same
#: "quote the device rather than guess" rule ``uiautomator_dump`` follows applies:
#: without this check an error string would be attached to the tester's chat as a
#: broken image while the packet's note claimed a picture was there.
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

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
    # ONE PARSER, TWO READERS. This used to re-derive `parts[1] == "device"`
    # beside the identical line in `device_manager._list_android`, and both
    # dropped an `unauthorized` phone in silence. The classification now happens
    # once; this caller keeps its contract -- serials it can DRIVE, nothing else
    # -- because every one of its callers (emulator boot-wait, emulator adopt,
    # the qa-doctor lock probe) asks exactly that question. The states are
    # reported to the tester by `qa_list_devices`, which is where a person reads.
    rows = parse_adb_devices(str((result["content"] or {}).get("out") or ""))
    if rows.dropped_lines:
        # THE SAME REFUSAL SHAPE AS A NON-ZERO rc, for the same reason. A
        # truncated list cannot answer "which serials can I drive?": the one
        # the caller wants may be a line that was never read, and every caller
        # here spawns, adopts or reports on the answer -- `find_running` would
        # start a SECOND emulator over the tester's own (D1, 2026-09-03). All
        # four callers already branch on `error`, so the safe direction needs
        # no second sentinel.
        return {
            "error": (
                "adb listed more transports than this server reads ("
                + str(rows.dropped_lines)
                + " line(s) past the limit were not parsed), so it cannot say "
                "which devices are connected. Unplug what you are not using, "
                "or run `adb kill-server`, then try again."
            ),
            "content": None,
        }
    serials = [str(row["serial"]) for row in rows if row.get("usable")]
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


#: A locale tag this lane will SET on a device: a language subtag, optionally a
#: region. Narrow on purpose -- ``setprop`` accepts any string, and a device given
#: a tag no resources exist for renders in its default language while every
#: reader believes it was configured.
LOCALE_TAG = re.compile(r"^[a-z]{2}(-[A-Z]{2})?$")

#: The property that survives a reboot. NOT the property that says what the
#: screen is rendering in right now -- see :func:`runtime_locale`.
PERSIST_LOCALE_PROP = "persist.sys.locale"


def _tag_from_config(line: str) -> str:
    """The BCP-47 tag inside one ``am get-config`` line, or ``""``.

    ``config: mcc310-mnc260-en-rUS-ldltr-sw411dp-...``. Split on ``-`` and take
    the first token that is exactly two lowercase letters, plus the ``rXX``
    token after it when there is one. A regex over the whole line would have to
    exclude ``ldltr`` and every qualifier Android adds later by hand; the token
    walk cannot match one, because no qualifier is two letters long.
    """
    # DROP THE LABEL FIRST. `am get-config` answers `config: ar-rEG-ldltr-...`,
    # and splitting the raw line on `-` yields `config: ar` as one token -- two
    # words, not two letters -- so a locale in FIRST position was never found.
    # It only appeared to work where mcc/mnc came first and pushed the tag off
    # the label.
    text = str(line or "")
    _, _, after = text.partition(":")
    parts = [part for part in (after or text).strip().split("-") if part]
    for index, part in enumerate(parts):
        if len(part) != 2 or not part.isalpha() or not part.islower():
            continue
        following = parts[index + 1] if index + 1 < len(parts) else ""
        if len(following) == 3 and following[0] == "r" and following[1:].isupper():
            return part + "-" + following[1:]
        return part
    return ""


async def runtime_locale(serial: str) -> dict:
    """THE locale the device is RENDERING IN. ``{"error", "content": tag}``.

    ``am get-config`` reports the configuration apps actually resolve resources
    against, which is the only fact a report may echo: a run that says it ran in
    Arabic must mean the screens were Arabic, not that a property was written.

    A SECOND, WEAKER DERIVATION IS DELIBERATELY ABSENT. ``getprop
    persist.sys.locale`` answers a DIFFERENT question -- what survives a reboot
    -- and :func:`persisted_locale` owns it under its own name. Two names
    because the two legitimately disagree: writing the property on a running
    device changes the second and not the first until the device restarts, and
    that gap IS the failure this lane has to be able to see.

    An unparseable answer is ``""`` with NO error: an unknown locale is a blank
    in the report, never a guess and never a refused run.
    """
    result = await shell(serial, ["am", "get-config"], timeout=30)
    if result.get("error"):
        return result
    body = result.get("content") or {}
    rc = int(body.get("rc") or 0)
    if rc != 0:
        detail = (
            str(body.get("err") or "").strip() or str(body.get("out") or "").strip()
        )
        return {
            "error": "adb shell am get-config failed ("
            + (detail[:200] if detail else "exit " + str(rc))
            + ").",
            "content": None,
        }
    for line in str(body.get("out") or "").splitlines():
        if line.strip().startswith("config:"):
            return {"error": None, "content": _tag_from_config(line)}
    return {"error": None, "content": ""}


async def persisted_locale(serial: str) -> dict:
    """The locale that will survive a reboot: ``persist.sys.locale``, or ``""``.

    A second name for a second question -- see :func:`runtime_locale` for why
    these are not one function with a caveat.
    """
    return await getprop(serial, PERSIST_LOCALE_PROP)


async def set_locale(serial: str, tag: str) -> dict:
    """Ask the device for *tag*, then CONFIRM what it is rendering in.

    ``{"error", "content": {"runtime", "persisted"}}``.

    THE READ-BACK IS THE RESULT, and the exit code is not read as evidence at
    all. ``setprop persist.sys.locale`` exits 0 on builds where it changed
    nothing -- ``persist.sys.*`` needs root, which the ``google_apis_playstore``
    user build this lane provisions does not give adb -- and even where the
    write lands, the running system keeps its old configuration until it
    restarts. So this reports what :func:`runtime_locale` says AFTERWARDS and
    lets the caller compare.

    Guarded by the kill-switch at the innermost function that changes the
    device, exactly as ``install``/``uninstall`` are and for the reason
    ``emulator.start`` states: a guard on a caller is only as good as the list
    of callers.
    """
    if not settings.qa_mobile_run_enabled:
        return {
            "error": (
                "Refusing to change the device language: the mobile lane needs "
                "`QA_MOBILE_RUN_ENABLED=true` in `.env`. Nothing was changed."
            ),
            "content": None,
        }
    wanted = str(tag or "").strip()
    if not LOCALE_TAG.match(wanted):
        return {
            "error": (
                "Refusing to set the device language to "
                + repr(wanted[:40])
                + ". Give a language tag like `ar`, `ar-EG` or `en-US`."
            ),
            "content": None,
        }
    await shell(serial, ["setprop", PERSIST_LOCALE_PROP, wanted], timeout=30)
    seen = await runtime_locale(serial)
    if seen.get("error"):
        return seen
    persisted = await persisted_locale(serial)
    return {
        "error": None,
        "content": {
            "runtime": str(seen.get("content") or ""),
            "persisted": (
                "" if persisted.get("error") else str(persisted.get("content") or "")
            ),
        },
    }


async def device_facts(serial: str) -> dict:
    """``{kind, model, api}`` for one device. ``kind`` is ``emulator`` or ``physical``.

    WHY THE SERIAL PREFIX IS NOT ENOUGH, even though it is free. ``emulator-*``
    is how the local emulator presents itself over USB-style transport, but an
    emulator reached over TCP (``adb connect``, a remote or containerised one)
    arrives as ``host:port`` and would be reported as the tester's PHONE. The
    kind is the whole point of this record -- a pass on an emulator is weaker
    evidence than a pass on real hardware, and a mislabel makes the evidence
    worse than absent -- so it is CONFIRMED against ``ro.kernel.qemu``, which
    the device answers about itself.

    Three ``getprop`` reads, run ONCE per run at planning time and stored on the
    manifest, so nothing in the packet loop pays for them. Nothing here raises
    and nothing here is fatal: a property that cannot be read leaves its field
    empty, and an empty model is a worse report line, not a failed run.
    """
    text = str(serial or "").strip()
    facts = {"kind": "", "model": "", "api": ""}
    qemu = await getprop(text, "ro.kernel.qemu")
    is_qemu = str((qemu or {}).get("content") or "").strip() == "1"
    facts["kind"] = (
        "emulator" if (text.startswith("emulator-") or is_qemu) else "physical"
    )
    model = await getprop(text, "ro.product.model")
    if not (model or {}).get("error"):
        facts["model"] = str(model.get("content") or "")[:60]
    api = await getprop(text, "ro.build.version.sdk")
    if not (api or {}).get("error"):
        facts["api"] = str(api.get("content") or "")[:8]
    return {"error": None, "content": facts}


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
    """Every package id ``pm list packages`` reports.

    A NON-ZERO EXIT IS A FAILED PROBE, NOT AN EMPTY DEVICE. That is the rule
    ``devices`` above already states, from the same evidence and for the same
    reason: ``shell`` does not inspect the exit code, so a command that RAN and
    failed (device offline, no permission, a dead adb server) arrives here with
    ``error=None`` and no stdout. Returning ``[]`` for it told every caller the
    device ANSWERED and the app is simply not there, and
    ``session.install_state`` printed that as "`pkg` is not installed on the
    emulator" (2026-09-08).

    The rc is surfaced HERE rather than guessed at by each caller. An empty
    list is a plausible-looking answer, so a caller-side heuristic ("a real
    device lists hundreds of packages") would be a second, weaker derivation of
    a fact this module already holds exactly -- one question with two answers,
    which is how mirrored conditions drift.
    """
    result = await shell(serial, ["pm", "list", "packages"], timeout=60)
    if result.get("error"):
        return result
    body = result.get("content") or {}
    rc = int(body.get("rc") or 0)
    if rc != 0:
        detail = (
            str(body.get("err") or "").strip() or str(body.get("out") or "").strip()
        )
        return {
            "error": "adb shell pm list packages failed ("
            + (detail[:200] if detail else "exit " + str(rc))
            + ").",
            "content": None,
        }
    out: list[str] = []
    for line in str(body.get("out") or "").splitlines():
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


#: Plausibility bounds on a density the DEVICE reports. `wm density` is an
#: untrusted number like every other line this module parses, and dp -> px is a
#: MULTIPLIER: an absurd value silently rescales the accessibility floor instead
#: of failing, so it is rejected and the reader falls back to no answer.
MIN_DENSITY_DPI = 40
MAX_DENSITY_DPI = 1000
#: A bare integer on the line. Bounded on both sides for the same reason
#: `_DISPLAY_RE` is: an unbounded `\d+` reads a seven-digit slice of a longer
#: number as a plausible answer instead of refusing it.
_DENSITY_RE = re.compile(r"(?<![\d-])(\d{1,4})(?!\d)")
#: Per serial: `(monotonic deadline, dpi or None)`. Shares
#: `DISPLAY_CACHE_TTL_S` deliberately -- it is the same staleness question about
#: the same device, and a second window would be a second answer to "how long is
#: a device fact good for". A FAILED answer is cached for the same window, so a
#: `wm` hiccup costs one round trip rather than one per screen.
_DENSITY_CACHE: dict = {}


async def display_density(serial: str) -> dict:
    """The device's display density in DPI, or ``None`` content. Never raises.

    ``wm density`` prints ``Physical density: 420`` and, where a density
    override is set, an additional ``Override density: 480``. The override is
    what the framework actually lays out with, so it wins where both appear --
    and it wins on its OWN MERIT rather than by being printed second, exactly as
    `display_size` treats an override size. `tests/mobile/test_mobile_density.py`
    pins the two functions' precedence together, because two copies of one rule
    are two rules that drift.

    This exists because **dp is not in a uiautomator dump**. A dump gives pixels;
    every platform minimum a person cares about ("a touch target is 48dp") is in
    dp, and the conversion needs this number. Without it `screen_audit` has to
    ASSUME a density, and an assumed density misflags compliant controls on
    low-density hardware -- so this is read from the device for the same reason
    the display size is.

    Never refuses loudly: a device that cannot answer gets
    ``{"error": None, "content": None}`` and the caller falls back to a stated
    assumption. An accessibility finding must never cost a screen.
    """
    key = str(serial or "")
    now = time.monotonic()
    cached = _DENSITY_CACHE.get(key)
    if cached is not None and now < cached[0]:
        return {"error": None, "content": cached[1]}
    result = await shell(key, ["wm", "density"])
    dpi = None
    overridden = False
    if not result.get("error"):
        text = str((result.get("content") or {}).get("out") or "")
        for line in text.splitlines():
            lowered = line.strip().lower()
            override = lowered.startswith("override density:")
            if not override and not lowered.startswith("physical density:"):
                continue
            match = _DENSITY_RE.search(lowered)
            if not match:
                continue
            value = int(match.group(1))
            if not (MIN_DENSITY_DPI <= value <= MAX_DENSITY_DPI):
                continue
            if overridden and not override:
                continue
            dpi = value
            overridden = overridden or override
    _DENSITY_CACHE[key] = (now + DISPLAY_CACHE_TTL_S, dpi)
    return {"error": None, "content": dpi}


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


#: ONE device shell command, not two adb round trips.
#:
#: Worth ~2.1s per action, measured on mrun-20260905-051728: a `wait 500` took
#: 2564ms and a `wait 3000` took 5086ms. A wait sends no input at all, so the
#: ~2.06s both overshoot by is fixed overhead, and it was being paid once per
#: action.
#:
#: The dumper's STDOUT goes to /dev/null because it prints "UI hierchary dumped
#: to: <path>" and adb interleaves that line with the XML -- which is the whole
#: reason `uiautomator dump /dev/tty` was tried and REVERTED. That path is not
#: re-taken here: the XML still comes from `cat` of a staged file.
#:
#: `2>&1` is on the CAT, not on the dump, on purpose: it is what lets a run that
#: produced no file say so in the device's own words ("cat: ...: No such file")
#: instead of being reported as a secure window.
#:
#: Passed as ONE argv element. `shell` builds an argv LIST run by
#: `create_subprocess_exec`, so NO host shell is ever spawned on any platform
#: and `;` and `>` are never interpreted locally -- they reach the device's sh,
#: which is the point. One element rather than several because newer adb
#: shell-escapes individual arguments, which would turn `>/dev/null` into a
#: literal; a single quoted string is the universally supported form and is what
#: Windows' list2cmdline round-trips correctly through adb.exe.
_DUMP_COMMAND = (
    "uiautomator dump " + DUMP_REMOTE_PATH + " >/dev/null 2>&1; "
    "cat " + DUMP_REMOTE_PATH + " 2>&1"
)


async def uiautomator_dump(serial: str) -> dict:
    """The current screen's uiautomator XML, as a string.

    ONE adb round trip -- see :data:`_DUMP_COMMAND` for why it is shaped the way
    it is, and for the `/dev/tty` path this deliberately does NOT take.

    The two halves no longer have separate error RETURNS, because there is one
    call and adb reports one transport failure. What still distinguishes them is
    the device's own output, and it is quoted back rather than swallowed: a dump
    that never wrote a file leaves `cat` to speak, and those words LEAD the
    error rather than trailing an explanation that contradicts them; the secure
    window is named after them, as one cause. Only a dump that produced no
    output AT ALL is reported as a secure window first, because nothing else is
    known in that branch. `rc`
    was ignored on both calls before and is ignored now; with `;` it would be
    `cat`'s rc either way.
    """
    read = await shell(serial, [_DUMP_COMMAND], timeout=DUMP_TIMEOUT_S)
    if read.get("error"):
        return read
    xml = str((read["content"] or {}).get("out") or "")
    if not xml.lstrip().startswith("<"):
        said = " ".join(xml.split())[:200]
        if said:
            # The device SPOKE, so its words LEAD. `cat: ...: No such file`
            # means the dumper never wrote a file, and a reply that opens with
            # "a secure window blocks the dump" sends the tester's model to
            # change screens when the cause is elsewhere -- it reads the first
            # sentence. The secure window is named after, as one cause.
            return {
                "error": (
                    "The device said: "
                    + said
                    + " -- uiautomator produced no XML for this screen. If "
                    "those words name a missing dump file, the dumper did not "
                    "run; a secure window (a password field or a payment "
                    "sheet) is one cause, and moving past it or using a screen "
                    "that allows accessibility is the fix."
                ),
                "content": None,
            }
        # Nothing came back at all. There are no device words to lead with, so
        # the likeliest cause is all this branch has to offer.
        return {
            "error": (
                "uiautomator returned no XML for this screen and the device "
                "said nothing. A secure window (a password field or a payment "
                "sheet) blocks the dump; move past it or use a screen that "
                "allows accessibility."
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


async def screencap(serial: str) -> dict:
    """The current screen as PNG BYTES: ``{"error", "content": bytes|None}``.

    Never raises, exactly like every other function in this module, and a
    failure is never a partial image: no adb, a timeout, a non-zero exit, an
    over-cap payload and a body that is not a PNG all come back with
    ``content: None`` and a stated error, so the caller can carry on with the
    dump alone. A missing picture must degrade the packet, never lose the turn.

    **It calls :func:`_run_argv` DIRECTLY, and that is forced rather than
    chosen.** ``raw`` returns ``out.decode(errors="replace")``, so a PNG routed
    through ``raw`` / ``_device`` / ``shell`` comes back as replacement
    characters and can never be re-encoded -- the bytes are gone by the time
    this function would see them. ``_run_argv`` is still the module's SINGLE
    subprocess seam, so the test recorder in ``tests/mobile/conftest.py``
    records this call like any other; what is skipped is only the decoding
    layer. The serial validation ``_device`` would have done is done here, with
    the same ``_valid_device_id`` and the same wording, so nothing is lost by
    going round it.

    ``exec-out`` rather than ``shell`` for the reason :func:`run_as_cat` gives:
    ``shell`` is a pty and mangles the bytes.

    The timeout is the module DEFAULT rather than a new constant: a screencap is
    one short device call, and ``DEFAULT_TIMEOUT_S`` already carries the CEILINGS
    row stating exactly the constraint that applies to it.
    """
    if not _valid_device_id(serial):
        return {
            "error": "Refusing to use " + repr(str(serial)[:40]) + " as a device id.",
            "content": None,
        }
    try:
        cmd = [resolve_adb(), "-s", str(serial), "exec-out", "screencap", "-p"]
        rc, out, err = await _run_argv(cmd, DEFAULT_TIMEOUT_S)
    except FileNotFoundError:
        return {
            "error": "adb was not found, so no screen could be captured.",
            "content": None,
        }
    except asyncio.TimeoutError:
        return {
            "error": (
                "screencap did not answer within " + str(DEFAULT_TIMEOUT_S) + "s."
            ),
            "content": None,
        }
    except Exception as exc:
        logger.exception("mobile.adb.screencap failed")
        return {"error": str(exc), "content": None}
    if rc != 0:
        # The device's own words, like `uiautomator_dump` quotes `cat`'s. A
        # non-zero exit is checked BEFORE the magic below so a refusal is
        # reported as a refusal rather than as "that was not a PNG".
        said = " ".join(err.decode(errors="replace").split())[:200]
        return {
            "error": ("screencap exited " + str(rc) + ((": " + said) if said else ".")),
            "content": None,
        }
    if len(out) > MAX_SCREENSHOT_BYTES:
        return {
            "error": (
                "This screen's PNG exceeds the "
                + str(MAX_SCREENSHOT_BYTES)
                + " byte cap and was discarded."
            ),
            "content": None,
        }
    if not out.startswith(_PNG_MAGIC):
        said = " ".join(out[:200].decode(errors="replace").split())[:200]
        return {
            "error": (
                "screencap returned no PNG for this screen. A secure window (a "
                "password field or a payment sheet) blocks a capture."
                + ((" The device said: " + said) if said else "")
            ),
            "content": None,
        }
    return {"error": None, "content": out}


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


# ---- the emulator console, proxied by adb (plan mobile-network-capture) ------
#
# THE ONE SURFACE that can record a device's traffic without root: measured on
# emulator-5554 on 2026-09-09, ``adb root`` is refused on a production build,
# ``tcpdump`` is not on the system image and a release APK refuses ``run-as``.
# The emulator console CAN, because it runs in the emulator process on the host
# -- and ``adb ... emu <cmd>`` reaches it, carrying the console auth token
# itself, so nothing in this tree reads, holds or logs that credential.

#: What a run on a physical device is told, BY NAME. The lane supports a real
#: phone; the emulator console does not exist there, so a capture is IMPOSSIBLE
#: rather than empty -- and an empty section reads to a tester as "the app
#: called nothing", which is the false claim this constant exists to prevent.
NOT_AN_EMULATOR = (
    "network capture needs an emulator; this run is on a physical device, so "
    "the app network traffic was not recorded (its logcat still was)"
)

#: One word of a console command line. Deliberately narrower than the console
#: itself accepts: every argument sent here is a literal in this tree or a file
#: name this tree composed, so nothing needs a space, a quote or a newline.
_EMU_WORD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")

_BARE_PCAP_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}\.pcap$")


def valid_capture_name(name: object) -> bool:
    """True for a name the emulator console accepts as a bare file name.

    Checked HERE, before adb is called, even though the console checks it too:
    a name built from a run id must never be able to become a path this process
    asks a co-process to write, and the console's own refusal EXITS ZERO.
    """
    text = str(name or "")
    return bool(_BARE_PCAP_RE.match(text)) and ".." not in text


async def emu(serial: str, *args: object) -> dict:
    """``adb -s <serial> emu <args>``: the emulator console. Never raises.

    ``{"error", "content": [<reply line>, ...]}`` -- the reply with its trailing
    ``OK`` stripped, so a caller reads what the console SAID and not its
    punctuation.

    **THE EXIT CODE IS ALWAYS ZERO**, and this is the whole reason the function
    exists rather than a bare ``raw`` call at each site. Measured on
    emulator-5554 on 2026-09-09: ``emu network capture start ../x.pcap`` answers
    ``KO: <file> must be a bare filename ...`` and STILL exits 0. So the verdict
    is read from the TEXT and from nothing else -- a reply whose last non-empty
    line is exactly ``OK`` succeeded; any line beginning ``KO`` is a failure
    carrying its own reason. Branching on ``rc`` would report every refusal as a
    success, which is the one failure shape a capture must not have: a run that
    believes it is recording and is not.

    A reply cut by :data:`MAX_DUMP_BYTES` is an ERROR, not a truncated success.
    The terminator is the only evidence the command worked, and a reply whose
    end was cut cannot carry it.

    **The device must be an EMULATOR, and this is the ONE place that question is
    asked on this path.** ``device_facts`` confirms it against ``ro.kernel.qemu``
    rather than trusting the serial prefix, because an emulator reached over TCP
    arrives as ``host:port``; a second check at a caller would be a second
    derivation of one question, and mirrored conditions drift.
    """
    words = [str(word) for word in args]
    if not words:
        return {"error": "Refusing an empty emulator console command.", "content": None}
    for word in words:
        if not _EMU_WORD_RE.match(word):
            return {
                "error": "Refusing emulator console argument " + repr(word[:40]) + ".",
                "content": None,
            }
    facts = await device_facts(serial)
    kind = str(((facts or {}).get("content") or {}).get("kind") or "")
    if kind != "emulator":
        return {"error": NOT_AN_EMULATOR, "content": None}
    result = await _device(serial, ["emu"] + words)
    if result.get("error"):
        return result
    body = result["content"] or {}
    joined = str(body.get("out") or "")
    stderr = str(body.get("err") or "").strip()
    if stderr:
        joined = joined + "\n" + stderr
    text, truncated = _capped(joined, MAX_DUMP_BYTES)
    if truncated:
        return {
            "error": (
                "The emulator console answered with more than "
                + str(MAX_DUMP_BYTES)
                + " bytes, so its reply could not be read to the end and `"
                + " ".join(words)
                + "` cannot be reported as having worked."
            ),
            "content": None,
        }
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines:
        if line == "KO" or line.startswith("KO:"):
            return {
                "error": (
                    "The emulator refused `"
                    + " ".join(words)
                    + "`: "
                    + line[3:].strip()
                ),
                "content": None,
            }
    if not lines or lines[-1] != "OK":
        return {
            "error": (
                "The emulator console did not confirm `"
                + " ".join(words)
                + "`; its reply did not end in OK."
            ),
            "content": None,
        }
    return {"error": None, "content": lines[:-1]}
