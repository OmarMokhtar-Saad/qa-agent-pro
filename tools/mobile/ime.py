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

**3. An unpinned asset refuses BY NAME.** ``tools/mobile/ime_manifest.py`` is
Phase 0 Part B and does not exist until a release is cut, so :func:`manifest`
reports exactly that, with the fix, and every function that needs the APK
refuses the same way. Nothing here carries a placeholder hash: a hash nobody
published is not a weaker verification, it is the absence of one. When Part B
lands this module starts working with NO change here -- the import is by name,
resolved at call time.
"""

from __future__ import annotations

import base64
import importlib
import logging
import re
from pathlib import Path

from config.settings import settings
from tools.mobile import adb, downloader, paths

logger = logging.getLogger(__name__)

#: The module Phase 0 Part B adds. Imported by NAME, at call time.
MANIFEST_MODULE = "tools.mobile.ime_manifest"

#: Reason code callers (and preflight) branch on.
NOT_PINNED = "ime_not_pinned"

NOT_PINNED_DETAIL = (
    "The QA input method is not pinned yet: `"
    + MANIFEST_MODULE.replace(".", "/")
    + ".py` carries no published release asset URL and SHA-256, so the APK "
    "cannot be fetched or verified. Nothing was downloaded."
)

NOT_PINNED_FIX = (
    "Cut the qa-ime release (mobile programme Phase 0 Part B) and commit "
    "`tools/mobile/ime_manifest.py` with the release asset URL and the "
    "SHA-256 the build published. No change is needed in tools/mobile/ime.py: "
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
    if not settings.qa_mobile_run_enabled:
        return {
            "error": (
                "Refusing to fetch the QA IME: the mobile lane needs "
                "`QA_MOBILE_RUN_ENABLED=true` in `.env`. Nothing was "
                "downloaded."
            ),
            "content": None,
        }
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
    if not settings.qa_mobile_run_enabled:
        return {
            "error": (
                "Refusing to install the QA IME: the mobile lane needs "
                "`QA_MOBILE_RUN_ENABLED=true` in `.env`. Nothing was installed."
            ),
            "content": None,
        }
    fetched = ensure_apk()
    if fetched.get("error"):
        return fetched
    return await adb.install(serial, str((fetched["content"] or {})["path"]))


async def current_ime(serial: str) -> dict:
    """The device's currently selected input method id, or ``""``."""
    result = await adb.shell(
        serial, ["settings", "get", "secure", "default_input_method"]
    )
    if result.get("error"):
        return result
    text = str((result["content"] or {}).get("out") or "").strip()
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
            "was_ours": bool(ours) and previous == ours,
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
    if ours and target == ours:
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


async def type_text(serial: str, text: str, secret: bool = False) -> dict:
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
        payload = base64.b64encode(value.encode("utf-8")).decode("ascii")
        sent = await _broadcast(serial, str(actions["input"]), payload)
        if sent.get("error"):
            return sent
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


async def clear(serial: str) -> dict:
    """Clear the focused field through the IME."""
    resolved = manifest()
    if resolved.get("error"):
        return resolved
    actions = (resolved["content"] or {})["actions"]
    sent = await _broadcast(serial, str(actions["clear"]))
    if sent.get("error"):
        return sent
    return {"error": None, "content": {"cleared": True}}
