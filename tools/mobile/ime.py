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

from tools.mobile import adb, downloader, paths

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
            "actions": {
                "input": str(getattr(module, "ACTION_INPUT", package + ".INPUT")),
                "clear": str(getattr(module, "ACTION_CLEAR", package + ".CLEAR")),
                "query": str(getattr(module, "ACTION_QUERY", package + ".QUERY")),
            },
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
    """``ime enable`` for the pinned id (an IME must be enabled before it is set)."""
    resolved = manifest()
    if resolved.get("error"):
        return resolved
    ime_id = str((resolved["content"] or {})["ime_id"])
    return await adb.shell(serial, ["ime", "enable", ime_id])


async def select(serial: str) -> dict:
    """Make the pinned IME the active one."""
    resolved = manifest()
    if resolved.get("error"):
        return resolved
    return await adb.pm_select(serial, str((resolved["content"] or {})["ime_id"]))


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
    result = await adb.pm_select(serial, target)
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


async def _broadcast(serial: str, action: str, b64: str = "") -> dict:
    built = _broadcast_line(action, b64)
    if built.get("error"):
        return built
    # THE point of this module: an EMPTY argv plus stdin. The payload is never
    # an element of the host command line.
    return await adb.shell(serial, [], stdin_data=str(built["content"]).encode("utf-8"))


def _parse_reply(text: str) -> dict:
    """``{result, field, ime_visible, text}`` from a broadcast reply."""
    result_match = _RESULT_RE.search(text or "")
    result = int(result_match.group(1)) if result_match else -1
    data_match = _DATA_RE.search(text or "")
    data = data_match.group(1) if data_match else ""
    field = ""
    visible = False
    field_text = ""
    for part in str(data).split(";"):
        part = part.strip()
        if part.startswith("t:"):
            try:
                field_text = base64.b64decode(part[2:] or "", validate=True).decode(
                    "utf-8", errors="replace"
                )
            except Exception:
                field_text = ""
        elif part.startswith("f:"):
            field = part[2:]
        elif part.startswith("k:"):
            visible = part[2:].strip() == "1"
    return {
        "result": result,
        "field": field,
        "ime_visible": visible,
        "text": field_text,
    }


async def query(serial: str) -> dict:
    """Ask the oracle what it can see. ``result=0`` means NO input connection."""
    resolved = manifest()
    if resolved.get("error"):
        return resolved
    actions = (resolved["content"] or {})["actions"]
    sent = await _broadcast(serial, str(actions["query"]))
    if sent.get("error"):
        return sent
    payload = sent["content"] or {}
    raw_text = str(payload.get("out") or "") + str(payload.get("err") or "")
    return {"error": None, "content": _parse_reply(raw_text)}


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


async def _focused_field(serial: str, actions: dict) -> str:
    """Empty when the keyboard answers with a focused field, else why not."""
    sent = await _broadcast(serial, str(actions["query"]))
    if sent.get("error"):
        return str(sent["error"])
    code = _reply_code(sent)
    if code < 0:
        return NO_RECEIVER
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
    if _reply_code(sent) >= 0:
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
        if len(value) > MAX_TEXT_CHARS:
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
        resolved = manifest()
        if resolved.get("error"):
            return resolved
        actions = (resolved["content"] or {})["actions"]
        # `receiver_known`: the caller (executor.replay, via keyboard_up) has
        # already had the receiver answer once this run, so no per-type QUERY.
        # Encoded BEFORE any broadcast, so an encoding failure is reported as
        # itself rather than hidden behind a device round trip.
        payload = base64.b64encode(value.encode("utf-8")).decode("ascii")
        if not receiver_known:
            refused = await _focused_field(serial, actions)
            if refused:
                return {"error": refused, "content": None}
        sent = await _broadcast(serial, str(actions["input"]), payload)
        if sent.get("error"):
            return sent
        refused = await _undelivered(serial, actions, sent)
        if refused:
            return {"error": refused, "content": None}
        logger.info(
            "mobile.ime: typed %d character(s)%s",
            len(value),
            " (secret)" if secret else "",
        )
        return {
            "error": None,
            "content": {"typed": len(value), "secret": bool(secret)},
        }
    except Exception as exc:
        logger.exception("mobile.ime.type_text failed")
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
