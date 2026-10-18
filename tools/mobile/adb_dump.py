"""Screen dump: ``uiautomator_dump`` and its provider/refusal helpers (moved from adb.py).

``_DUMP_COMMAND`` and ``_DUMP_PROVIDERS`` live here once; the facade re-exports them."""

from __future__ import annotations

import logging
import time

from config.settings_guard import never_raise
from tools import tool_status
from tools.mobile import a11y_events, a11y_reread, dump_latency_state, step_timing
from tools.mobile.adb_consts import DUMP_REMOTE_PATH, FIRST_DUMP_TIMEOUT_S
from tools.mobile.adb_host import host

logger = logging.getLogger("tools.mobile.adb")


#: ONE device shell command, not two adb round trips.
#:
#: Worth ~2.1s per action, measured on mrun-20260905-051728: a `wait 500` took
#: 2564ms and a `wait 3000` took 5086ms. A wait sends no input at all, so the
#: ~2.06s both overshoot by is fixed overhead, and it was being paid once per
#: action.
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


#: Per-serial fast dump providers (the qa-ime AccessibilityService, see a11y.py).
#: A provider answers ``{"error", "content": xml}``; anything but a clean,
#: capped, ``<``-led answer falls through to the uiautomator path below.
_DUMP_PROVIDERS: dict = {}


def set_dump_provider(serial: str, provider) -> None:
    _DUMP_PROVIDERS[str(serial)] = provider


def clear_dump_provider(serial: str) -> None:
    _DUMP_PROVIDERS.pop(str(serial), None)
    a11y_events.record(serial, None)  # uiautomator dumps carry no event clock


#: What the step timing row says served a dump (the manifest ``source``). The
#: fallback tags say WHY uiautomator was used, so a slow run is explainable.
SOURCE_A11Y = "qa-ime-a11y"
SOURCE_NO_PROVIDER = "uiautomator-dump (no a11y)"
SOURCE_PROVIDER_FAILED = "uiautomator-dump (a11y failed)"


def _clean(fast: dict) -> str | None:
    """The provider's XML when it is clean, capped and ``<``-led, else None."""
    fast_xml = str(fast.get("content") or "")
    capped = len(fast_xml.encode("utf-8", errors="replace")) <= host().MAX_DUMP_BYTES
    if not fast.get("error") and fast_xml.lstrip().startswith("<") and capped:
        return fast_xml
    logger.warning("mobile.adb: a11y dump refused: %s", str(fast.get("error"))[:200])
    return None


async def _provider_dump(serial: str, provider) -> str | None:
    """The fast provider's XML when it is clean, else ``None`` (also when the
    provider raises) so the caller falls through to uiautomator. A failure's
    time is ledgered as ``provider_failed``: it is lost before the fallback."""
    start = time.monotonic()
    xml = None
    with never_raise(
        logger, "mobile.adb: dump provider failed; using uiautomator", exc_info=True
    ):
        xml = _clean(await provider(serial) or {})
    if xml is None:
        step_timing.note("provider_failed", int((time.monotonic() - start) * 1000))
    return xml


def _device_said(said: str) -> dict:
    """The refusal that quotes the device's own words first."""
    # The device SPOKE, so its words LEAD (the first sentence is what the model reads).
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


def _dump_no_xml(said: str) -> dict:
    """The refusal for a dump that did not produce XML; *said* is the device's
    own (collapsed, capped) output, possibly empty."""
    if said:
        return _device_said(said)
    # Nothing came back at all. There are no device words to lead with, so the
    # likeliest cause is all this branch has to offer.
    return {
        "error": (
            "uiautomator returned no XML for this screen and the device "
            "said nothing. A secure window (a password field or a payment "
            "sheet) blocks the dump; move past it or use a screen that "
            "allows accessibility."
        ),
        "content": None,
    }


async def _fast_dump(serial: str, use_provider: bool) -> dict | None:
    """The provider's reply envelope when it answered cleanly, else ``None``."""
    provider = _DUMP_PROVIDERS.get(str(serial)) if use_provider else None
    if provider is None:
        if use_provider:
            step_timing.note_source(SOURCE_NO_PROVIDER)
        return None
    fast_xml = await _provider_dump(serial, provider)
    if fast_xml is None:
        step_timing.note_source(SOURCE_PROVIDER_FAILED)
        return None
    step_timing.note_source(SOURCE_A11Y)
    return {"error": None, "content": fast_xml}


def _over_cap() -> dict:
    """The refusal for a dump larger than ``MAX_DUMP_BYTES``."""
    return {
        "error": (
            "This screen's uiautomator dump exceeds the "
            + str(host().MAX_DUMP_BYTES)
            + " byte cap and was discarded unparsed."
        ),
        "content": None,
    }


def _dump_reply(read: dict) -> dict:
    """The ``uiautomator_dump`` envelope for the one ``shell`` read."""
    if read.get("error"):
        if read.get("timed_out"):
            # Same rule as `screencap` (fix round 3, item 3): a dump that
            # timed out is a distinct status on the call, not an `ok`.
            tool_status.mark_timed_out("uiautomator_dump")
        return read
    xml = str((read["content"] or {}).get("out") or "")
    if not xml.lstrip().startswith("<"):
        return _dump_no_xml(" ".join(xml.split())[:200])
    if len(xml.encode("utf-8", errors="replace")) > host().MAX_DUMP_BYTES:
        return _over_cap()
    return {"error": None, "content": xml}


# ONE adb round trip -- see :data:`_DUMP_COMMAND` for why it is shaped the way
# it is, and for the `/dev/tty` path this deliberately does NOT take.
#
# The two halves no longer have separate error RETURNS, because there is one
# call and adb reports one transport failure. What still distinguishes them is
# the device's own output, and it is quoted back rather than swallowed: a dump
# that never wrote a file leaves `cat` to speak, and those words LEAD the
# error rather than trailing an explanation that contradicts them; the secure
# window is named after them, as one cause. Only a dump that produced no
# output AT ALL is reported as a secure window first, because nothing else is
# known in that branch. `rc`
# was ignored on both calls before and is ignored now; with `;` it would be
# `cat`'s rc either way.
async def _bounded_dump(serial: str) -> dict:
    """A dump cut at FIRST_DUMP_TIMEOUT_S (animation), retried once at the full one."""
    read = await host().shell(serial, [_DUMP_COMMAND], timeout=FIRST_DUMP_TIMEOUT_S)
    if read.get("timed_out"):
        read = await host().shell(
            serial, [_DUMP_COMMAND], timeout=host().DUMP_TIMEOUT_S
        )
    return read


async def uiautomator_dump(serial: str, *, use_provider: bool = True) -> dict:
    """The current screen's uiautomator XML, as a string."""
    fast = await _fast_dump(serial, use_provider)
    fast = fast or (a11y_reread.take(serial) if use_provider else None)
    dump_latency_state.mark_slow(serial, use_provider and not fast)
    return fast or _dump_reply(await _bounded_dump(serial))
