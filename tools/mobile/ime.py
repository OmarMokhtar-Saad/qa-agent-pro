"""The QA input method: install, select, restore, and the state oracle.

Three properties are load-bearing and each is pinned by a test.

**1. A secret never reaches argv.** ``type_text(..., secret=True)`` base64-encodes
the text in Python and hands the whole ``am broadcast`` command line to
``adb shell`` on **stdin**. Command lines are world-readable on every OS this
lane supports (``ps``, Windows' process list, and adb's own logging), so a
password passed as an argument is a password disclosed. The base64 payload is
additionally checked against the base64 alphabet before it is sent, so no
crafted text can close the quote it sits in.

**2. The previous input method is restored, whatever it was.** On the machine
this phase was measured on, the emulator's default IME belongs to a THIRD-PARTY
package that has nothing to do with qa-agents. Restore therefore reads and
replays whatever was there -- it never assumes ours was the pre-existing one,
and an absent previous value is reported rather than papered over.

**3. An unpinned asset refuses BY NAME.** ``tools/mobile/ime_manifest.py`` pins
the published qa-ime release asset URL and its SHA-256. :func:`manifest`
imports it by name at call time; if it is missing or incomplete (a stripped
build, a deleted file) :func:`manifest` says so, with the fix, and every
function that needs the APK refuses the same way. Nothing here carries a
placeholder hash: a hash nobody published is not a weaker verification, it is
the absence of one.
"""

from __future__ import annotations

import base64
import importlib
import logging
import re
from pathlib import Path

from tools.mobile import adb, downloader, ime_nonce, paths
from tools.untrusted import wrap_untrusted

logger = logging.getLogger(__name__)

#: The module that pins the published qa-ime APK. Imported by NAME, at call time.
MANIFEST_MODULE = "tools.mobile.ime_manifest"

#: Reason code callers (and preflight) branch on.
NOT_PINNED = "ime_not_pinned"

NOT_PINNED_DETAIL = (
    "The QA input method is not pinned yet on this install: `"
    + MANIFEST_MODULE.replace(".", "/")
    + ".py` is missing or carries no release asset URL and SHA-256, so the "
    "APK cannot be fetched or verified. Nothing was downloaded."
)

NOT_PINNED_FIX = (
    "Restore `tools/mobile/ime_manifest.py` from the release this install "
    "came from (it pins the qa-ime release asset URL and its SHA-256), or "
    "reinstall. No change is needed in tools/mobile/ime.py: "
    "it resolves that module by name at call time, so this check starts "
    "passing the moment the module lands."
)

#: Standard base64 alphabet, anchored. A payload that does not match is not sent.
_B64_RE = re.compile(r"^[A-Za-z0-9+/]*={0,2}$")

#: Cap on a single typed string. Longer than any credential and short enough
#: that a broadcast command line stays inside every OS's argument limits.
MAX_TEXT_CHARS = 4000

#: ``result=`` / ``data="..."`` in an ``am broadcast`` reply.
_RESULT_RE = re.compile(r"result=(-?\d+)")
_DATA_RE = re.compile(r'data="(.*)"', re.DOTALL)

#: Android's ``ime`` command can answer a refusal on stdout at rc 0 ("Unknown
#: input method <id>"), so an rc check alone is not enough for `enable`
#: either -- same phrases as adb.pm_select's own copy, declared separately so
#: neither module imports the other's private regex.
_IME_REFUSAL_RE = re.compile(
    r"unknown input method|cannot be (?:enabled|set)|error", re.IGNORECASE
)


def _manifest_actions(module: object, package: str) -> dict:
    """The broadcast action names, each defaulting to ``<package>.<NAME>``."""
    return {
        "input": str(getattr(module, "ACTION_INPUT", package + ".INPUT")),
        "clear": str(getattr(module, "ACTION_CLEAR", package + ".CLEAR")),
        "query": str(getattr(module, "ACTION_QUERY", package + ".QUERY")),
        # Batch 4b: only the new APK answers these; 1.0.0 ignores them.
        "arm": str(getattr(module, "ACTION_ARM", package + ".ARM")),
        "disarm": str(getattr(module, "ACTION_DISARM", package + ".DISARM")),
        "dump": str(getattr(module, "ACTION_DUMP", package + ".DUMP")),
    }


def manifest() -> dict:
    """The pinned IME identity, or a refusal naming the missing pin.

    ``{"error", "content": {"version", "package", "service", "ime_id", "url",
    "sha256", "actions": {...}}}``. The error path additionally carries
    ``reason``/``fix`` on the ``content`` of :func:`manifest_status` so a
    preflight can render a fix line without string-matching this message.
    """
    try:
        module = importlib.import_module(MANIFEST_MODULE)
    except ImportError:
        return {"error": NOT_PINNED_DETAIL, "content": None}
    except Exception as exc:  # pragma: no cover - a broken manifest module
        logger.exception("mobile.ime: %s could not be imported", MANIFEST_MODULE)
        return {"error": str(exc), "content": None}
    package = str(getattr(module, "IME_PACKAGE", "") or "")
    service = str(getattr(module, "IME_SERVICE", "") or "")
    url = str(getattr(module, "IME_ASSET_URL", "") or "")
    sha = str(getattr(module, "IME_SHA256", "") or "").strip().lower()
    version = str(getattr(module, "IME_VERSION", "") or "")
    if not package or not service:
        return {
            "error": (
                MANIFEST_MODULE
                + " exists but names no IME package/service, so nothing can be "
                "installed or selected."
            ),
            "content": None,
        }
    if not downloader.valid_sha256(sha) or not url.startswith("https://"):
        return {"error": NOT_PINNED_DETAIL, "content": None}
    return {
        "error": None,
        "content": {
            "version": version,
            "package": package,
            "service": service,
            "ime_id": package + "/" + service,
            "url": url,
            "sha256": sha,
            "actions": _manifest_actions(module, package),
        },
    }


def manifest_status() -> dict:
    """``{ok, reason, detail, fix}`` -- the preflight-friendly view of the pin."""
    resolved = manifest()
    if not resolved.get("error"):
        content = resolved["content"] or {}
        return {
            "error": None,
            "content": {
                "ok": True,
                "reason": "",
                "detail": "pinned: "
                + str(content.get("ime_id"))
                + " @ "
                + str(content.get("version") or "unversioned"),
                "fix": "",
            },
        }
    return {
        "error": None,
        "content": {
            "ok": False,
            "reason": NOT_PINNED,
            "detail": str(resolved["error"]),
            "fix": NOT_PINNED_FIX,
        },
    }


#: How much of an input-method id a REPORT prints. Ids are `package/.Class` and
#: the package is a reverse domain, so an untruncated one pushes the fact a
#: tester needs -- installed, selected -- off the end of a narrow table cell.
#: DISPLAY ONLY: nothing compares, stores or selects a truncated id, and
#: `same_component` is always handed the full string.
IME_ID_DISPLAY_CHARS = 60


def display_id(value: object) -> str:
    """An input-method id, shortened for a report cell.

    In this package rather than at each surface: qa-doctor's prose and
    `machine_report`'s rows both print this id, and two independent truncations
    of one value is how two surfaces start disagreeing about what the device
    said.
    """
    text = str(value or "").strip()
    if len(text) <= IME_ID_DISPLAY_CHARS:
        return text
    return text[: IME_ID_DISPLAY_CHARS - 1] + "\u2026"


def apk_cache_path(version: str) -> Path:
    """Where the verified APK is cached."""
    tag = re.sub(r"[^A-Za-z0-9._-]", "-", str(version or "unversioned"))
    return paths.sub("ime") / ("qa-ime-" + tag + ".apk")


def ensure_apk() -> dict:
    """Return the cached, hash-verified APK path, downloading it on a miss.

    Kill-switch checked HERE, at the effect: a cache miss fetches over the
    network, and the flag's documented promise is that it gates every install,
    download or launch. A guard only on the provisioner covers the path one
    reviewer walked, not the class.
    """
    resolved = manifest()
    if resolved.get("error"):
        return resolved
    info = resolved["content"] or {}
    paths.ensure_tree()
    dest = apk_cache_path(str(info.get("version") or ""))
    return downloader.download(
        str(info["url"]),
        dest,
        str(info["sha256"]),
        payload_bytes=0,
        progress_path=paths.state_file("ime-download.json"),
        phase="ime",
    )


async def installed(serial: str) -> dict:
    """True when the pinned IME package is present on the device."""
    resolved = manifest()
    if resolved.get("error"):
        return resolved
    package = str((resolved["content"] or {})["package"])
    listed = await adb.installed_packages(serial)
    if listed.get("error"):
        return listed
    return {
        "error": None,
        "content": {"installed": package in (listed.get("content") or [])},
    }


# `pm path` answers `package:<absolute path>`. That path goes into a device shell
# argv, so a hostile answer must not reach it: only this shape is accepted.
_PM_PATH_RE = re.compile(r"package:(/[A-Za-z0-9_.=~+@/-]{1,300})")
_SHA256_TOKEN_RE = re.compile(r"[0-9a-fA-F]{64}")


def _verdict(pinned: bool | None, detail: str) -> dict:
    """One verify_pinned answer. ``pinned`` None means NOT DETERMINED."""
    return {"error": None, "content": {"pinned": pinned, "detail": detail}}


def _shell_out(result: dict) -> str | None:
    """stdout of a device command that ran and exited 0, else None."""
    body = result.get("content") or {}
    if result.get("error") or body.get("rc") not in (0, None):
        return None
    return str(body.get("out") or "")


async def _installed_path(serial: str, package: str) -> str | dict:
    """The device path of the installed APK, or a verdict that ends the check."""
    found = await adb.shell(
        serial, ["pm", "path", package], timeout=adb.PM_LIST_TIMEOUT_S
    )
    out = _shell_out(found)
    if out is None:
        return _verdict(None, "pm path failed")
    if not out.strip():
        return _verdict(None, "pm path printed nothing")
    lines = [ln.strip() for ln in out.splitlines() if ln.strip().startswith("package:")]
    if len(lines) > 1:
        return _verdict(False, "installed as a split APK; the pinned build is one file")
    matched = _PM_PATH_RE.fullmatch(lines[0]) if lines else None
    if matched is None:
        return _verdict(None, "pm path answered an unexpected shape")
    return matched.group(1)


async def verify_pinned(serial: str) -> dict:
    """Is the package on the device the PINNED build, not just one with its name?

    ``installed`` matches the package name only, so any app installed under the
    keyboard's package id would be enabled and selected and then receive every
    typed value, credentials included. This hashes the installed file on the
    device and compares it with the manifest's sha256 (case-insensitive).

    ``{"error": None, "content": {"pinned": True | False | None, "detail"}}``.
    None means could not be determined (the manifest has no usable hash, `pm path`
    failed or was silent or of an unexpected shape, no `sha256sum`, no hex digest)
    and is never read as a match or as a mismatch. A split install is False: the
    pinned build is a single file. With no usable hash this returns None BEFORE
    any adb call. Never raises on a bad answer; adb errors become None.
    """
    info = manifest().get("content") or {}
    want = str(info.get("sha256") or "").strip().lower()
    if not downloader.valid_sha256(want):
        return _verdict(None, "the manifest names no usable hash")
    located = await _installed_path(serial, str(info.get("package") or ""))
    if isinstance(located, dict):
        return located
    hashed = await adb.shell(
        serial, ["sha256sum", located], timeout=adb.PM_LIST_TIMEOUT_S
    )
    tokens = (_shell_out(hashed) or "").split()
    if not tokens or not _SHA256_TOKEN_RE.fullmatch(tokens[0]):
        return _verdict(None, "sha256sum gave no digest")
    if tokens[0].lower() == want:
        return _verdict(True, "the installed build matches the pinned hash")
    return _verdict(False, "the installed build differs from the pinned hash")


async def is_foreign_build(serial: str) -> bool:
    """True ONLY when the device proved the installed build is not the pinned one.

    Unverifiable is logged and answers False: a device without `sha256sum` must
    not be reinstalled on every run, and the pinned APK is still what the host
    downloads and verifies.
    """
    verdict = (await verify_pinned(serial)).get("content") or {}
    if verdict.get("pinned") is None:
        logger.warning(
            "mobile.ime: could not verify the installed keyboard against the "
            "pinned build (%s)",
            verdict.get("detail"),
        )
    return verdict.get("pinned") is False


async def install(serial: str) -> dict:
    """Install the pinned APK from cache (fetching it first on a miss).

    Guarded in its own right rather than relying on ensure_apk's check: a
    warm cache would otherwise install without the flag ever being read.
    """
    fetched = ensure_apk()
    if fetched.get("error"):
        return fetched
    return await adb.install(serial, str((fetched["content"] or {})["path"]))


def same_component(left: object, right: object) -> bool:
    """Do two Android component ids name the SAME component?

    `pkg/.Class` and `pkg/pkg.Class` are the same thing: a class name beginning
    with `.` is relative to the package. Android accepts either and STORES the
    shorthand -- measured 2026-09-04 on a real device, setting the fully
    qualified id and reading `settings get secure default_input_method` back:

        set: io.qaagents.ime/io.qaagents.ime.QaImeService
        got: io.qaagents.ime/.QaImeService

    So a string comparison against the pinned id is false on every install, and
    `preflight`'s `ime_selected` check refused every run on a device where the
    QA keyboard was correctly installed, enabled and active. The whole suite was
    green because the fake answered with the expanded form, which no device
    ever returns.

    Blank never matches blank: an absent id is not evidence of anything, and two
    unknowns are not the same component.
    """

    def _expand(value: object) -> str:
        text = str(value or "").strip()
        package, sep, cls = text.partition("/")
        if not sep or not cls:
            return text
        if cls.startswith("."):
            cls = package + cls
        return package + "/" + cls

    a, b = _expand(left), _expand(right)
    return bool(a) and a == b


async def current_ime(serial: str) -> dict:
    """The device's currently selected input method id, or ``""``."""
    result = await adb.shell(
        serial, ["settings", "get", "secure", "default_input_method"]
    )
    if result.get("error"):
        return result
    body = result.get("content") or {}
    # A NON-ZERO EXIT IS A FAILED PROBE, NOT "(none)" -- the rule `adb.devices`
    # and `adb.installed_packages` already state. `shell` does not inspect the
    # exit code, so a `settings get` that RAN and failed arrived here with
    # `error=None` and empty stdout, and preflight rendered
    # "active input method: (none)" for a device whose keyboard it never asked.
    # The consumer's guard cannot fire on evidence the producer never sets.
    rc = int(body.get("rc") or 0)
    if rc != 0:
        detail = (
            str(body.get("err") or "").strip() or str(body.get("out") or "").strip()
        )
        return {
            "error": "adb settings get default_input_method failed ("
            + (detail[:200] if detail else "exit " + str(rc))
            + ").",
            "content": None,
        }
    text = str(body.get("out") or "").strip()
    if text.lower() in ("", "null"):
        return {"error": None, "content": ""}
    return {"error": None, "content": text}


#: One id per line from ``ime list -a -s``; ``ime list -a`` prints one block
#: per input method, headed ``pkg/.Class:`` and carrying ``mId=pkg/.Class``.
_IME_ID_RE = re.compile(
    r"^\s*(?:mId=)?([A-Za-z0-9_.]+/[A-Za-z0-9_.$]+):?(?=\s|$)", re.MULTILINE
)

#: The QA keyboard is installed but the device does not list it under the
#: pinned component, so ``ime enable``/``ime set`` would only be refused.
IME_NOT_LISTED = (
    "The QA keyboard (%s) is not among the input methods this device lists "
    "(%s), so it cannot be enabled or selected and nothing can be typed. Start "
    "the run again with apply=true so the server reinstalls the pinned QA "
    "keyboard; if it is still not listed, report this message as a defect."
)


async def _list_ime_ids(serial: str) -> list[str]:
    """Every input method id the device lists, spelled as the device spells it.

    ``ime list -a -s`` first, ``ime list -a`` when that fails or lists nothing.
    Empty when neither answered: the caller decides what an unread list means.
    """
    for argv in (["ime", "list", "-a", "-s"], ["ime", "list", "-a"]):
        result = await adb.shell(serial, argv)
        if result.get("error"):
            continue
        body = result.get("content") or {}
        if int(body.get("rc") or 0) != 0:
            continue
        ids: list[str] = []
        for found in _IME_ID_RE.findall(str(body.get("out") or "")):
            if found not in ids:
                ids.append(found)
        if ids:
            return ids
    return []


async def resolve_installed_id(serial: str) -> dict:
    """The QA keyboard's id AS THE DEVICE LISTS IT, for ``enable``/``select``.

    The manifest pins ``pkg/pkg.Class`` and a device stores and lists
    ``pkg/.Class``; the air session's API 35 device refused the pinned
    spelling on ``ime enable`` and nothing was ever typed. So the id sent is
    the LISTED one that :func:`same_component` matches, never the pin itself.

    A list that names other keyboards but not ours is a refusal by name
    (:data:`IME_NOT_LISTED`). A list that could not be read at all proves
    nothing, so the pinned id is sent and the device's own answer decides.
    """
    resolved = manifest()
    if resolved.get("error"):
        return resolved
    pinned = str((resolved["content"] or {})["ime_id"])
    listed = await _list_ime_ids(serial)
    if not listed:
        logger.warning(
            "mobile.ime: the device listed no input methods; sending the pin"
        )
        return {"error": None, "content": pinned}
    for candidate in listed:
        if same_component(candidate, pinned):
            return {"error": None, "content": candidate}
    return {
        "error": IME_NOT_LISTED % (pinned, ", ".join(listed[:8])[:400]),
        "content": None,
    }


async def remember_previous(serial: str) -> dict:
    """Read the input method to restore later, BEFORE selecting ours.

    ``{"previous": <id or "">, "was_ours": bool}``. On this project's own
    emulator the answer is a third-party keyboard, so nothing downstream may
    treat an unfamiliar value as an error.
    """
    resolved = manifest()
    ours = str((resolved.get("content") or {}).get("ime_id") or "")
    current = await current_ime(serial)
    if current.get("error"):
        return current
    previous = str(current.get("content") or "")
    return {
        "error": None,
        "content": {
            "previous": previous,
            # By component identity, not spelling: the device reports the
            # shorthand `pkg/.Class` and the manifest pins the expanded form,
            # so `==` answered "that is not our keyboard" about our own
            # keyboard -- and the run would then try to 'restore' it as if it
            # were the tester's.
            "was_ours": same_component(previous, ours),
        },
    }


async def enable(serial: str) -> dict:
    """``ime enable`` for the pinned id (an IME must be enabled before it is set).

    Checks the device's actual answer the same way :func:`current_ime` already
    does in this file: a non-zero ``rc`` OR a refusal phrase in the combined
    stdout/stderr is a failure, since ``ime enable`` can exit 0 while printing
    a refusal.
    """
    resolved = await resolve_installed_id(serial)
    if resolved.get("error"):
        return resolved
    ime_id = str(resolved["content"])
    result = await adb.shell(serial, ["ime", "enable", ime_id])
    if result.get("error"):
        return result
    body = result.get("content") or {}
    rc = int(body.get("rc") or 0)
    combined = str(body.get("out") or "") + str(body.get("err") or "")
    if rc != 0 or _IME_REFUSAL_RE.search(combined):
        return {
            "error": "adb ime enable failed: " + combined.strip()[:400],
            "content": None,
        }
    return {"error": None, "content": {}}


async def select(serial: str) -> dict:
    """Make the pinned IME the active one, by the id the device lists."""
    resolved = await resolve_installed_id(serial)
    if resolved.get("error"):
        return resolved
    return await adb.pm_select(serial, str(resolved["content"]))


async def restore_previous(serial: str, previous: str) -> dict:
    """Re-select whatever was active before the run.

    A blank *previous* is a REPORTED no-op: the device had no default input
    method recorded, and inventing one would leave the tester's emulator in a
    state we chose for them. A *previous* that is our own id is also a no-op --
    restoring it would be pointless, and it is exactly the case a test must not
    be allowed to assume.
    """
    resolved = manifest()
    ours = str((resolved.get("content") or {}).get("ime_id") or "")
    target = str(previous or "").strip()
    if not target:
        return {
            "error": None,
            "content": {
                "restored": False,
                "previous": "",
                "detail": (
                    "No previous input method was recorded for this device, so "
                    "none was restored."
                ),
            },
        }
    if same_component(target, ours):
        return {
            "error": None,
            "content": {
                "restored": False,
                "previous": target,
                "detail": "The QA input method was already the default before this run.",
            },
        }
    # Best effort: the listed spelling of the same component when the device
    # lists one, else the value exactly as it was read.
    # A malformed id is refused by pm_select without a device call;
    # listing first would spend one on a value that cannot be sent.
    listed = await _list_ime_ids(serial) if adb.is_ime_id(target) else []
    send = next((c for c in listed if same_component(c, target)), target)
    result = await adb.pm_select(serial, send)
    if result.get("error"):
        return result
    return {
        "error": None,
        "content": {
            "restored": True,
            "previous": target,
            "detail": "restored " + target,
        },
    }


def _broadcast_line(action: str, b64: str = "") -> dict:
    """Build the ``am broadcast`` shell line, or refuse.

    Returns ``{"error", "content": <line>}``. The payload is validated against
    the base64 alphabet HERE, before it is placed inside single quotes, so the
    quoting cannot be broken by any input.
    """
    if not re.match(r"^[A-Za-z0-9._]{1,120}$", str(action or "")):
        return {
            "error": "Refusing to broadcast action " + repr(str(action)[:60]),
            "content": None,
        }
    if b64:
        if not _B64_RE.match(b64):
            return {
                "error": (
                    "Refusing to broadcast a payload that is not valid base64; "
                    "nothing was sent."
                ),
                "content": None,
            }
        line = "am broadcast -a " + action + " --es msg '" + b64 + "'\n"
    else:
        line = "am broadcast -a " + action + "\n"
    return {"error": None, "content": line}


async def _send(serial: str, action: str, b64: str = "", nonce: str = "") -> dict:
    built = _broadcast_line(action, b64)
    if built.get("error"):
        return built
    line = str(built["content"])
    if nonce:
        # Validated BEFORE it is quoted into the shell line, like the payload.
        if not ime_nonce.valid_nonce(nonce):
            return {
                "error": "Refusing to broadcast a malformed nonce; nothing was sent.",
                "content": None,
            }
        line = line.rstrip("\n") + " --es nonce '" + nonce + "'\n"
    # THE point of this module: an EMPTY argv plus stdin. The payload and the
    # nonce are never an element of the host command line.
    return await adb.shell(serial, [], stdin_data=line.encode("utf-8"))


def reply_of(sent: dict) -> dict:
    """The parsed reply of a broadcast envelope; no field text is revealed."""
    payload = sent.get("content") or {}
    if not isinstance(payload, dict):
        return _parse_reply("", reveal_text=False)
    raw = str(payload.get("out") or "") + str(payload.get("err") or "")
    return _parse_reply(raw, reveal_text=False)


def _refusal_of(sent: dict) -> str:
    reply = reply_of(sent)
    if int(reply.get("result", -1)) != ime_nonce.REPLY_REFUSED:
        return ""
    return str(reply.get("refusal") or "unknown")


async def _broadcast(serial: str, action: str, b64: str = "") -> dict:
    """Send one broadcast with this serial's nonce, if it holds one.

    ``not_armed`` (idle timeout, restart) re-arms with a FRESH nonce and retries
    ``REARM_MAX_RETRIES`` times; ``bad_nonce`` is never retried.
    """
    nonce = ime_nonce.get(serial)
    sent = await _send(serial, action, b64, nonce)
    for _ in range(ime_nonce.REARM_MAX_RETRIES):
        if not nonce or sent.get("error") or _refusal_of(sent) != "not_armed":
            break
        if (await arm(serial, fresh=True)).get("error"):
            return sent
        nonce = ime_nonce.get(serial)
        sent = await _send(serial, action, b64, nonce)
    return sent


async def send(serial: str, action: str) -> dict:
    """Public seam for the a11y module: one authorised broadcast."""
    return await _broadcast(serial, action)


def _decode_field_text(payload: str) -> str:
    """The base64 ``t:`` payload as text, or ``""`` when it does not decode."""
    try:
        return base64.b64decode(payload or "", validate=True).decode(
            "utf-8", errors="replace"
        )
    except Exception:
        return ""


def _reply_fields(data: str) -> dict:
    """The ``;``-separated ``k:value`` parts of a reply, with defaults."""
    fields: dict = {
        "field": "",
        "visible": False,
        "text": "",
        "refusal": "",
        "protocol": 0,
        "dump": "",
    }
    for part in data.split(";"):
        part = part.strip()
        if part.startswith("t:"):
            fields["text"] = _decode_field_text(part[2:])
        elif part.startswith("f:"):
            fields["field"] = part[2:]
        elif part.startswith("k:"):
            fields["visible"] = part[2:].strip() == "1"
        elif part.startswith("e:"):
            fields["refusal"] = part[2:].strip()[:40]
        elif part.startswith("p:"):
            fields["protocol"] = int(part[2:]) if part[2:].strip().isdigit() else 0
        elif part.startswith("x:"):
            fields["dump"] = part[2:].strip()
    return fields


def _parse_reply(text: str, reveal_text: bool = True) -> dict:
    """``{result, field, ime_visible, text}`` from a broadcast reply.

    **``reveal_text=False`` is the SECRET path, and it is structural.** Under
    it the ``t:`` payload is decoded, MEASURED, and the plaintext is bound to
    nothing that outlives this function: the returned dict carries ``text_len``
    and has **no ``text`` key at all**. Absent, not empty -- a consumer
    reaching for it raises ``KeyError`` rather than silently reading ``""`` and
    concluding the field was blank.

    The existing secret guarantee (module docstring, ``adb.py``) is OUTBOUND: a
    secret never reaches argv on the way TO the device. This mode is the
    INBOUND half, opened the moment ``type_text`` began querying a secret
    field to see whether its text landed.
    """
    result_match = _RESULT_RE.search(text or "")
    result = int(result_match.group(1)) if result_match else -1
    data_match = _DATA_RE.search(text or "")
    data = data_match.group(1) if data_match else ""
    parts = _reply_fields(str(data))
    field_text = parts["text"]
    reply = {
        "result": result,
        "field": parts["field"],
        "ime_visible": parts["visible"],
    }
    # Present only when non-empty, so a legacy reply keeps its exact shape.
    for key in ("refusal", "protocol", "dump"):
        if parts[key]:
            reply[key] = parts[key]
    if reveal_text:
        reply["text"] = field_text
    else:
        reply["text_len"] = len(field_text)
    return reply


async def query(serial: str, reveal_text: bool = True) -> dict:
    """Ask the oracle what it can see. ``result=0`` means NO input connection.

    ``reveal_text=False`` returns ``text_len`` instead of ``text``; see
    ``_parse_reply``.
    """
    resolved = manifest()
    if resolved.get("error"):
        return resolved
    actions = (resolved["content"] or {})["actions"]
    sent = await _broadcast(serial, str(actions["query"]))
    if sent.get("error"):
        return sent
    # A refusal still standing after the re-arm retries is not an answer: parsed as
    # one, its missing field would read as empty and the probe would report OK.
    refused = _refusal_of(sent)
    if refused:
        return {"error": ime_nonce.refusal_text(refused), "content": None}
    payload = sent["content"] or {}
    raw_text = str(payload.get("out") or "") + str(payload.get("err") or "")
    return {"error": None, "content": _parse_reply(raw_text, reveal_text=reveal_text)}


async def probe(serial: str) -> dict:
    """``{ok, result, field, ime_visible}`` -- the preflight's oracle check.

    ``ok`` is True for result 0 AND 1: both are ANSWERS. Only ``-1`` (no
    ``result=`` in the reply at all) means the service did not respond, which
    is the failure a preflight must catch.
    """
    answered = await query(serial)
    if answered.get("error"):
        return answered
    content = answered["content"] or {}
    return {
        "error": None,
        "content": {
            "ok": int(content.get("result", -1)) >= 0,
            "result": int(content.get("result", -1)),
            "field": str(content.get("field") or ""),
            "ime_visible": bool(content.get("ime_visible")),
        },
    }


#: A broadcast reply with no ``result=`` line: nothing received it.
NO_RECEIVER = (
    "The QA keyboard did not answer (no result= in the broadcast reply), so "
    "nothing was typed or cleared: it is not installed, enabled or selected."
)

#: QaImeService.onQuery sets result 0 when there is no input connection.
NO_FOCUSED_FIELD = (
    "The QA keyboard answered that no field has input focus (result=0), so "
    "nothing was typed or cleared. Target the field by its visible label."
)


def _reply_code(sent: dict) -> int:
    """The ``result=`` code of a broadcast envelope; -1 when there is none."""
    payload = sent.get("content") or {}
    if not isinstance(payload, dict):
        return -1
    raw = str(payload.get("out") or "") + str(payload.get("err") or "")
    return int(_parse_reply(raw)["result"])


#: The three values of the ``landed`` verdict. THREE, not a bool, and the
#: reason is the middle one: a masked or formatted field (a phone or card
#: formatter, a password mask) legitimately holds something other than what
#: was sent, and calling that ``LANDED_NO`` would fail real runs on working
#: apps. ``LANDED_UNKNOWN`` is the honest answer there -- and the executor
#: treats it as a non-success too, so the ambiguity reaches the tester instead
#: of being resolved silently in our favour.
LANDED_YES = "yes"
LANDED_NO = "no"
LANDED_UNKNOWN = "unknown"


async def _field_snapshot(serial: str, actions: dict, secret: bool):
    """What the focused field holds, or ``None`` when the keyboard cannot say.

    ``None`` for every unanswerable case -- broadcast error, no ``result=``,
    ``result=0`` (no input connection) -- so one sentinel means "no reading",
    and the verdict never has to distinguish a failure from an empty field.

    For a secret this returns an INT (the length) and never the text; see
    ``_parse_reply``'s ``reveal_text``.
    """
    sent = await _broadcast(serial, str(actions["query"]))
    if sent.get("error"):
        return None
    payload = sent.get("content") or {}
    raw = str(payload.get("out") or "") + str(payload.get("err") or "")
    reply = _parse_reply(raw, reveal_text=not secret)
    if int(reply.get("result", -1)) != 1:
        return None
    return reply["text_len"] if secret else reply["text"]


#: What a field's own formatter adds or drops: a phone mask's dashes and
#: parentheses, a currency field's spaces, a trailing space a trim ate, an
#: underscore. Stripped from BOTH sides before comparing, so ``123-456`` and
#: ``123456`` are the same text.
_SEPARATORS = re.compile(r"[\W_]+")


def _could_be_transform(after: str, value: str) -> bool:
    """Could *after* be what this field MADE of *value*?

    Asked only about an UNCHANGED field that does not contain the value, where
    before/after comparison has run out: it cannot separate "nothing was
    committed" from "a transformed commit landed on what was already there".
    This decides which of the two to assume.

    The arms are not symmetric, so neither are the errors. A false ``True``
    turns a ``no`` into an ``unknown`` and costs ONE model round trip to look
    at the screen. A false ``False`` turns a transformed commit into a ``no``,
    and the executor ENDS THE CASE -- a passing run dies. So this leans
    towards ``True``, and each clause is a shape a real field produces:

    * an EMPTY field is the one thing no commit can leave behind, so it is the
      only certain ``False`` -- and it is asked FIRST, because
      ``value.startswith("")`` is True for every value, which would otherwise
      read an untouched empty field as a truncation;
    * ``value.startswith(after)`` is a ``maxLength`` cap, which keeps a prefix;
    * separators and case stripped from both sides catches a mask, a trim and
      a field that upper- or lower-cases what it accepts.
    """
    if not after:
        return False
    if value.startswith(after):
        return True
    stripped = _SEPARATORS.sub("", after).casefold()
    return stripped == _SEPARATORS.sub("", value).casefold()


def _secret_landed_verdict(before: int, after: int, value: str) -> str:
    """The ``landed`` verdict for a secret field, from LENGTHS only."""
    if after - before == len(value):
        return LANDED_YES
    # EMPTY after the commit is the one shape no commit leaves behind,
    # whatever the field held before -- asked before the changed/unchanged
    # split, which read a field emptied by a failed commit as ``unknown``.
    if after == 0:
        return LANDED_NO
    if after != before:
        return LANDED_UNKNOWN
    # Unchanged length, and only the length is knowable here. A
    # replace-on-focus of a field that already held something this long,
    # and a capped field that kept a truncation of what was sent, are both
    # indistinguishable from nothing happening. An EMPTY field is the one
    # shape no commit can leave behind, so it is all that is left of ``no``.
    return LANDED_NO if after == 0 else LANDED_UNKNOWN


def _landed_verdict(before, after, value: str, secret: bool) -> str:
    """Did *value* land in the field? ``yes`` / ``no`` / ``unknown``.

    An empty *value* is ``unknown``: there is nothing to look for, and calling
    a no-op ``yes`` would let a step that typed nothing report success -- the
    exact defect this verdict exists to end.

    The SECRET arm compares LENGTHS ONLY; the plaintext is never held, diffed
    or returned. Its accepted weakness is named in docs/MOBILE_TESTING.md: a
    field holding unrelated pre-existing content of a matching length (autofill
    is the realistic case) can read as ``yes``. The non-secret arm does not
    have that weakness because it compares content.

    **A COMMIT CAN REPLACE, NOT ONLY APPEND.** `QaImeService` calls
    `commitText`, which overwrites the current SELECTION, and
    `executor._perform` taps the field to focus it immediately before typing --
    the gesture that triggers `selectAllOnFocus`. So "the field did not change"
    does NOT imply "nothing happened": re-typing a value the field already
    held is a correct type with an unchanged field. Containment is therefore
    asked FIRST, and that collision resolves to ``unknown``, never ``no``.
    **AN UNCHANGED FIELD WITHOUT THE VALUE IS STILL NOT PROOF.** A field that
    TRANSFORMS what it accepts -- a phone mask, a ``maxLength`` cap, a trim, a
    case fold -- can take *value* and land on text the field already held, and
    then it holds neither its old contents unchanged by accident nor the value
    itself. Before/after comparison cannot separate that from "nothing was
    committed", so `_could_be_transform` decides which to assume and ``no``
    narrows to an unchanged field whose contents no transform of *value* could
    be. This matters because the arms are not symmetric: ``unknown`` asks the
    model to look, while ``no`` ends the case, so a false ``no`` kills a
    working run.

    The SECRET arm cannot ask that question at all -- only LENGTHS are knowable
    there -- so ``no`` narrows further still, to a field that is EMPTY after
    the commit. A field capped at two characters lands ``s3`` of ``s3cret``
    without moving its length, and nothing here can tell that from a dead one.
    """
    if before is None or after is None or not value:
        return LANDED_UNKNOWN
    if secret:
        return _secret_landed_verdict(int(before), int(after), value)
    if not str(after):
        return LANDED_NO
    if value in str(after):
        return LANDED_YES if after != before else LANDED_UNKNOWN
    if after != before:
        return LANDED_UNKNOWN
    return LANDED_UNKNOWN if _could_be_transform(str(after), value) else LANDED_NO


async def _focused_field(serial: str, actions: dict) -> str:
    """Empty when the keyboard answers with a focused field, else why not."""
    sent = await _broadcast(serial, str(actions["query"]))
    if sent.get("error"):
        return str(sent["error"])
    code = _reply_code(sent)
    if code < 0:
        return NO_RECEIVER
    if code == ime_nonce.REPLY_REFUSED:
        return ime_nonce.refusal_text(_refusal_of(sent))
    if code == 0:
        return NO_FOCUSED_FIELD
    return ""


async def _undelivered(serial: str, actions: dict, sent: dict) -> str:
    """Why an INPUT/CLEAR reply did not land, or empty when it did.

    Only a reply with NO ``result=`` is a failure; its VALUE is not read,
    because QaImeService.onInput/onClear set no result code and ``am
    broadcast`` reports its default on every delivery (whether the text
    LANDED is the open item in docs/DECISIONS.md). On that path ONE query
    tells a dead receiver from a field that lost focus.
    """
    code = _reply_code(sent)
    if code == ime_nonce.REPLY_REFUSED:
        # The new APK refused (not armed / wrong nonce): NOT a delivery, even
        # though 4 >= 0. Named, so the tester is told why nothing was typed.
        return ime_nonce.refusal_text(_refusal_of(sent))
    if code >= 0:
        return ""
    return (await _focused_field(serial, actions)) or NO_RECEIVER


async def type_text(
    serial: str, text: str, secret: bool = False, receiver_known: bool = False
) -> dict:
    """Commit *text* into the focused field through the IME.

    ``secret=True`` changes NOTHING about the transport -- the payload always
    travels on stdin -- and changes everything about what is said about it: the
    return value never echoes the text, and the log line records only a length.
    Keeping one transport for both means the secret path is the path every test
    exercises, rather than a rarely-taken branch.
    """
    try:
        value = "" if text is None else str(text)
        too_long = _length_refusal(value)
        if too_long:
            return too_long
        resolved = manifest()
        if resolved.get("error"):
            return resolved
        actions = (resolved["content"] or {})["actions"]
        return await _commit_and_verify(serial, actions, value, secret, receiver_known)
    except Exception as exc:
        logger.exception("mobile.ime.type_text failed")
        return {"error": str(exc), "content": None}


def _length_refusal(value: str) -> dict | None:
    """The refusal for a *value* over ``MAX_TEXT_CHARS``, else ``None``."""
    if len(value) <= MAX_TEXT_CHARS:
        return None
    return {
        "error": (
            "Refusing to type "
            + str(len(value))
            + " characters; the limit is "
            + str(MAX_TEXT_CHARS)
            + "."
        ),
        "content": None,
    }


async def _commit_and_verify(
    serial: str, actions: dict, value: str, secret: bool, receiver_known: bool
) -> dict:
    """Send *value* to the keyboard and report what the field made of it."""
    # `receiver_known`: the caller (executor.replay, via keyboard_up) has
    # already had the receiver answer once this run, so no per-type QUERY.
    # Encoded BEFORE any broadcast, so an encoding failure is reported as
    # itself rather than hidden behind a device round trip.
    payload = base64.b64encode(value.encode("utf-8")).decode("ascii")
    if not receiver_known:
        refused = await _focused_field(serial, actions)
        if refused:
            return {"error": refused, "content": None}
    # BEFORE the commit, because the verdict is a DIFFERENCE. A snapshot
    # taken only afterwards cannot tell a field that accepted the text
    # from one that already held it.
    before = await _field_snapshot(serial, actions, secret)
    sent = await _broadcast(serial, str(actions["input"]), payload)
    if sent.get("error"):
        return sent
    refused = await _undelivered(serial, actions, sent)
    if refused:
        return {"error": refused, "content": None}
    after = await _field_snapshot(serial, actions, secret)
    landed = _landed_verdict(before, after, value, secret)
    logger.info(
        "mobile.ime: typed %d character(s)%s, landed=%s",
        len(value),
        " (secret)" if secret else "",
        landed,
    )
    return {
        "error": None,
        # ``typed`` is what the HOST SENT. ``landed`` is what the DEVICE
        # ACCEPTED. Two questions, two names -- reporting only the first is
        # how a run passed having typed nothing.
        "content": {
            "typed": len(value),
            "secret": bool(secret),
            "landed": landed,
        },
    }


def fallback_notice(reason: str, serial: str = "") -> str:
    """What a tester is told when the QA keyboard could not be set up (R1d).

    States THAT typing fell back to ``adb shell input text``, WHY, what that costs,
    and the exact command that fixes it. The run is not blocked.
    """
    target = str(serial or "").strip() or "<serial>"
    apk = ""
    try:
        version = str(((manifest() or {}).get("content") or {}).get("version") or "")
        if version and Path(apk_cache_path(version)).is_file():
            apk = str(apk_cache_path(version))
    except Exception:
        logger.debug("mobile.ime: no apk path for the fallback notice", exc_info=True)
    head = (
        "The QA keyboard is not available on this device, so typing fell back to "
        "`adb shell input text` (printable ASCII only, and the text cannot be read "
        "back to confirm it landed). Why: "
        # The reason is adb's or the device's own text, so it is data for the reader.
        + (
            wrap_untrusted("adb", " ".join(str(reason or "").split()), limit=300)
            or "unknown"
        )
    )
    tail = " Taps, swipes and screen checks are unaffected."
    if not apk:
        # No APK on disk: an install command would name a file that is not
        # there, so the fix is the download, not `install -r`.
        return (
            head + " Fix: the QA keyboard APK was not downloaded to this machine; start"
            " the run again once this machine is online so it can be fetched." + tail
        )
    return (
        head
        + " Fix: run `adb -s "
        + target
        + ' install -r "'
        + apk
        + '"` and start the run again.'
        + tail
    )


_INPUT_UNSAFE_RE = re.compile(r"[^\x20-\x7e]|%")


def _unsafe_input_refusal(value: str) -> dict | None:
    """The refusal for text ``input text`` cannot carry, else ``None``."""
    if not _INPUT_UNSAFE_RE.search(value):
        return None
    return {
        "error": (
            "The fallback typing path (`adb shell input text`) types printable "
            "ASCII only and cannot type `%`, so this text was not typed. Install "
            "the QA keyboard to type it (see the note about the QA keyboard)."
        ),
        "content": None,
        # A refusal the model can route around (different text, or ask the
        # tester), like the password refusal: NOT a device error that
        # ends the case.
        "needs_model": True,
    }


async def type_via_input(
    serial: str, text: str, secret: bool = False, receiver_known: bool = True
) -> dict:
    """The fallback typer: ``input text`` fed to ``adb shell`` over STDIN (R1d).

    The payload rides on stdin and never becomes an argv entry, exactly like
    :func:`type_text`. Printable ASCII only (``input text`` cannot carry anything
    else), and ``%`` is refused because ``input`` reads ``%s`` as a space. The
    verdict is always ``unknown``: nothing reads the field back, so the caller's
    landed check sends the step to the model instead of passing it silently.
    ``receiver_known`` is accepted for signature parity and ignored.
    """
    try:
        value = "" if text is None else str(text)
        refused = _length_refusal(value) or _unsafe_input_refusal(value)
        if refused:
            return refused
        quoted = "'" + value.replace(" ", "%s").replace("'", "'\\''") + "'"
        sent = await adb.shell(
            serial, [], stdin_data=("input text " + quoted + "\n").encode("ascii")
        )
        if sent.get("error"):
            return sent
        logger.info(
            "mobile.ime: typed %d character(s) by input text%s",
            len(value),
            " (secret)" if secret else "",
        )
        return {
            "error": None,
            "content": {
                "typed": len(value),
                "secret": bool(secret),
                "landed": LANDED_UNKNOWN,
                "fallback": True,
            },
        }
    except Exception as exc:
        logger.exception("mobile.ime.type_via_input failed")
        return {"error": str(exc), "content": None}


async def clear(serial: str, receiver_known: bool = False) -> dict:
    """Clear the focused field through the IME."""
    resolved = manifest()
    if resolved.get("error"):
        return resolved
    actions = (resolved["content"] or {})["actions"]
    if not receiver_known:
        refused = await _focused_field(serial, actions)
        if refused:
            return {"error": refused, "content": None}
    sent = await _broadcast(serial, str(actions["clear"]))
    if sent.get("error"):
        return sent
    refused = await _undelivered(serial, actions, sent)
    if refused:
        return {"error": refused, "content": None}
    return {"error": None, "content": {"cleared": True}}


#: Said when ARM finds the keyboard already armed and this process holds no nonce.
ALREADY_ARMED = (
    "The QA keyboard is already armed by another session (a run that crashed "
    "leaves it armed until its idle timeout), so typing falls back to `adb "
    "shell input text`. Wait about five minutes or reselect the keyboard, then "
    "start again."
)

#: Said when ARM answers with anything that is neither the new APK's arm nor the silence
#: of 1.0.0. Such a keyboard is NOT ready: no unnonced broadcast is ever sent to it.
UNEXPECTED_ARM_REPLY = (
    "The QA keyboard gave an unexpected reply to its arming request, so it is treated "
    "as not ready and typing falls back to `adb shell input text` for this run"
)


async def arm(serial: str, fresh: bool = False) -> dict:
    """Arm the keyboard with a new nonce; detect the APK generation by the reply.

    ``content`` is ``{"armed", "protocol"}``. ONLY ``result=0`` (what 1.0.0 returns for ARM,
    an action it does not know) means legacy: nothing is held and the legacy path is used
    unchanged. ``result=1`` with ``p:2`` or more arms. ``result=2`` is the named
    already-armed error. EVERYTHING else (``result=-1`` unparseable or lost, ``result=1``
    without ``p:``, any other code) is an error, never legacy: the callers turn an error
    into the run's one sticky fallback to ``input text`` with a notice. With a nonce
    already held (and ``fresh`` false) ONE nonce-bearing QUERY decides whether it is still
    ours before any second ARM.
    """
    try:
        resolved = manifest()
        if resolved.get("error"):
            return resolved
        actions = (resolved["content"] or {})["actions"]
        held = ime_nonce.get(serial)
        if held and not fresh and await _still_ours(serial, actions, held):
            return {
                "error": None,
                "content": {"armed": True, "protocol": ime_nonce.protocol(serial)},
            }
        ime_nonce.forget(serial)
        nonce = ime_nonce.new_nonce()
        sent = await _send(serial, str(actions["arm"]), "", nonce)
        if sent.get("error"):
            return sent
        return _arm_outcome(serial, nonce, reply_of(sent))
    except Exception as exc:
        logger.exception("mobile.ime.arm failed")
        return {"error": str(exc), "content": None}


async def _still_ours(serial: str, actions: dict, held: str) -> bool:
    """Does one nonce-bearing QUERY show the keyboard still armed for *held*?"""
    asked = await _send(serial, str(actions["query"]), "", held)
    return not asked.get("error") and _reply_code(asked) in (0, 1)


def _arm_outcome(serial: str, nonce: str, reply: dict) -> dict:
    """Interpret an ARM reply, holding the nonce when the keyboard armed."""
    code = int(reply.get("result", -1))
    if (
        code == ime_nonce.REPLY_OK
        and int(reply.get("protocol", 0)) >= ime_nonce.PROTOCOL_NONCE
    ):
        ime_nonce.hold(serial, nonce)
        ime_nonce.set_protocol(serial, int(reply["protocol"]))
        return {
            "error": None,
            "content": {"armed": True, "protocol": int(reply["protocol"])},
        }
    if code == ime_nonce.REPLY_ALREADY_ARMED:
        return {"error": ALREADY_ARMED, "content": None}
    if code == 0:
        ime_nonce.set_protocol(serial, ime_nonce.PROTOCOL_LEGACY)
        return {
            "error": None,
            "content": {"armed": False, "protocol": ime_nonce.PROTOCOL_LEGACY},
        }
    return {
        "error": UNEXPECTED_ARM_REPLY + " (result " + str(code) + ").",
        "content": None,
    }


async def disarm(serial: str) -> dict:
    """Disarm the keyboard. The nonce is forgotten FIRST: a disarm that cannot reach the
    device must not leave a usable secret here. Never raises."""
    try:
        held = ime_nonce.get(serial)
        ime_nonce.forget(serial)
        if not held:
            return {"error": None, "content": {"disarmed": False}}
        resolved = manifest()
        if resolved.get("error"):
            return resolved
        actions = (resolved["content"] or {})["actions"]
        sent = await _send(serial, str(actions["disarm"]), "", held)
        if sent.get("error"):
            return sent
        return {"error": None, "content": {"disarmed": True}}
    except Exception as exc:
        logger.exception("mobile.ime.disarm failed")
        return {"error": str(exc), "content": None}
