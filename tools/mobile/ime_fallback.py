"""The ``input text`` fallback: the notice a tester reads and the typer itself."""

from __future__ import annotations

import logging
from pathlib import Path

from config.settings_guard import never_raise
from tools.mobile import adb
from tools.mobile.ime_consts import _INPUT_UNSAFE_RE, LANDED_UNKNOWN, TypeOpts
from tools.mobile.ime_host import host
from tools.mobile.ime_type import _length_refusal
from tools.untrusted import wrap_untrusted

logger = logging.getLogger("tools.mobile.ime")


def _cached_apk() -> str:
    """The QA keyboard APK path when it is on disk, else an empty string."""
    with never_raise(
        logger, "mobile.ime: no apk path for the fallback notice", exc_info=True
    ):
        version = str(
            ((host().manifest() or {}).get("content") or {}).get("version") or ""
        )
        if version and Path(host().apk_cache_path(version)).is_file():
            return str(host().apk_cache_path(version))
    return ""


def _fallback_head(reason: str) -> str:
    """THAT typing fell back to ``input text``, and WHY."""
    return (
        "The QA keyboard is not available on this device, so typing fell back to "
        "`adb shell input text` (printable ASCII only, and the text cannot be read "
        "back to confirm it landed). Why: "
        # The reason is adb's or the device's own text, so it is data for the reader.
        + (
            wrap_untrusted("adb", " ".join(str(reason or "").split()), limit=300)
            or "unknown"
        )
    )


def _fallback_fix(apk: str, target: str) -> str:
    """The exact command that fixes it, or the download when there is no APK."""
    if not apk:
        # No APK on disk: an install command would name a file that is not
        # there, so the fix is the download, not `install -r`.
        return (
            " Fix: the QA keyboard APK was not downloaded to this machine; start"
            " the run again once this machine is online so it can be fetched."
        )
    return (
        " Fix: run `adb -s "
        + target
        + ' install -r "'
        + apk
        + '"` and start the run again.'
    )


# What a tester is told when the QA keyboard could not be set up (R1d).
#
# States THAT typing fell back to ``adb shell input text``, WHY, what that costs,
# and the exact command that fixes it. The run is not blocked.
def fallback_notice(reason: str, serial: str = "") -> str:
    """What a tester is told when the QA keyboard could not be set up (R1d)."""
    target = str(serial or "").strip() or "<serial>"
    tail = " Taps, swipes and screen checks are unaffected."
    return _fallback_head(reason) + _fallback_fix(_cached_apk(), target) + tail


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


def _input_line(value: str) -> bytes:
    """The ``input text`` line fed to ``adb shell`` over stdin."""
    quoted = "'" + value.replace(" ", "%s").replace("'", "'\\''") + "'"
    return ("input text " + quoted + "\n").encode("ascii")


def _fallback_typed(value: str, secret: bool) -> dict:
    """Log and report a fallback typing: the verdict is always ``unknown``."""
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


# The fallback typer: ``input text`` fed to ``adb shell`` over STDIN (R1d).
#
# The payload rides on stdin and never becomes an argv entry, exactly like
# :func:`type_text`. Printable ASCII only (``input text`` cannot carry anything
# else), and ``%`` is refused because ``input`` reads ``%s`` as a space. The
# verdict is always ``unknown``: nothing reads the field back, so the caller's
# landed check sends the step to the model instead of passing it silently.
# ``receiver_known`` is accepted for signature parity and ignored.
async def type_via_input(serial: str, text: str, opts: TypeOpts = TypeOpts()) -> dict:
    """The fallback typer: ``input text`` fed to ``adb shell`` over STDIN (R1d)."""
    try:
        value = "" if text is None else str(text)
        refused = _length_refusal(value) or _unsafe_input_refusal(value)
        if refused:
            return refused
        sent = await adb.shell_in(serial, [], adb.StdinFeed(_input_line(value)))
        if sent.get("error"):
            return sent
        return _fallback_typed(value, opts.secret)
    except Exception as exc:
        logger.exception("mobile.ime.type_via_input failed")
        return {"error": str(exc), "content": None}
