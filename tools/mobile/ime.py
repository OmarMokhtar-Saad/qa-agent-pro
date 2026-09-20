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
    reply = {
        "result": result,
        "field": field,
        "ime_visible": visible,
    }
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
        if int(after) - int(before) == len(value):
            return LANDED_YES
        if int(after) != int(before):
            return LANDED_UNKNOWN
        # Unchanged length, and only the length is knowable here. A
        # replace-on-focus of a field that already held something this long,
        # and a capped field that kept a truncation of what was sent, are both
        # indistinguishable from nothing happening. An EMPTY field is the one
        # shape no commit can leave behind, so it is all that is left of ``no``.
        return LANDED_NO if int(after) == 0 else LANDED_UNKNOWN
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
