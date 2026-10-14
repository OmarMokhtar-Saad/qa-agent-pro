"""Mobile session: the language record (locale) -- build, normalise, apply, phrase. Cut verbatim from tools/mobile/session_records.py (w17 sessA)."""

from __future__ import annotations

import logging
from typing import Any, NamedTuple

from tools.mobile import (
    adb,
)

logger = logging.getLogger("tools.mobile.session")


#: ``locale.source`` -- the one sentinel this module invents for the language
#: fact, with exactly three values. Consumers: the manifest written by the two
#: planners, ``report._locale_cell`` (which branches on all three) and
#: :func:`locale_phrase` (which deliberately does NOT branch on it -- a tester
#: reading the chat line needs the language and whether it took, not how it got
#: there, and that is the right ``else`` rather than an oversight).
LOCALE_FROM_BOOT = "boot"
LOCALE_FROM_SETPROP = "setprop"
LOCALE_FROM_DEVICE = "device"


def _same_language(actual: object, wanted: object) -> bool:
    """Does *actual* satisfy the request *wanted*?

    A request WITH a region must match exactly: a tester who asked for ``ar-EG``
    and got ``ar-SA`` has different date, number and currency formats, which is
    frequently the thing under test. A request without one is satisfied by any
    region of that language. Empty on either side is never a match -- an unknown
    locale is not a satisfied one.
    """
    got = str(actual or "").strip()
    ask = str(wanted or "").strip()
    if not got or not ask:
        return False
    if "-" in ask:
        return got == ask
    return got.split("-")[0] == ask


class LocaleFacts(NamedTuple):
    """The five raw facts one language record is built from."""

    requested: object
    actual: object
    persisted: object
    source: object
    detail: object


class _LocaleCase(NamedTuple):
    """What ``_locale_changed`` needs: wanted, actual and where the request came from."""

    wanted: str
    actual: str
    source: str


# THE shape of the language record, built in one place.
#
# ``matched`` is a POSITIVE claim made only from a read-back that answered:
# an unanswerable probe leaves it False, so no consumer can turn "we could not
# ask" into "the run was in Arabic". Same rule, same reason, as
# ``install_state.probed``.
def _locale_body(facts: LocaleFacts) -> dict:
    """The one shape of the language record (see the comment above)."""
    return {
        "requested": str(facts.requested or ""),
        "actual": str(facts.actual or ""),
        "persisted": str(facts.persisted or ""),
        "source": str(facts.source or LOCALE_FROM_DEVICE),
        "matched": (
            _same_language(facts.actual, facts.requested)
            if facts.requested
            else bool(str(facts.actual or ""))
        ),
        "detail": str(facts.detail or "")[:200],
    }


def locale_record(body: object) -> dict:
    """Normalise a language record for the manifest. Never raises.

    The counterpart of :func:`device_record`: one shape written by the two
    planners and read by every consumer, so a reader never has to ask which of
    two spellings a manifest used.
    """
    given = body if isinstance(body, dict) else {}
    return _locale_body(
        LocaleFacts(
            requested=given.get("requested"),
            actual=given.get("actual"),
            persisted=given.get("persisted"),
            source=given.get("source"),
            detail=given.get("detail"),
        )
    )


# Put the device into *requested* and report what it is ACTUALLY in.
#
# ``{"error", "content": {requested, actual, persisted, source, matched, detail}}``
#
# Three outcomes, and they are deliberately not one:
#
# * nothing requested -> one read, and the record says what the device was
#   already in (``source=device``);
# * requested and the device is already in it -> confirmed, no write;
# * requested and not -> ``adb.set_locale`` is attempted and the answer is
#   whatever the device says afterwards, never the exit code.
#
# This function never refuses; it REPORTS. The refusal is the caller's, in
# ``mcp_handlers._mobile_locale_stage``, because only the caller knows whether
# a run is about to be started on the strength of it.
async def apply_locale(
    serial: str, requested: str = "", *, booted_with: str = ""
) -> dict:
    """Put the device into *requested* and report what it is ACTUALLY in."""
    try:
        content = await _locale_content(serial, requested, booted_with)
        return {"error": None, "content": content}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.session.apply_locale failed")
        return {"error": str(exc), "content": None}


def _locale_source(wanted: str, booted_with: str) -> str:
    """Where the request came from: the boot flag or a later setprop."""
    return (
        LOCALE_FROM_BOOT
        if str(booted_with or "").strip() == wanted
        else LOCALE_FROM_SETPROP
    )


async def _locale_confirm(serial: str, case: _LocaleCase) -> dict:
    """Confirm a satisfied request, or write it and report what the device says."""
    if _same_language(case.actual, case.wanted):
        persisted = await adb.persisted_locale(serial)
        saved = "" if persisted.get("error") else str(persisted.get("content") or "")
        facts = LocaleFacts(case.wanted, case.actual, saved, case.source, "")
        return _locale_body(facts)
    return await _locale_changed(serial, case)


async def _locale_content(serial: str, requested: str, booted_with: str) -> dict:
    """The language record for one ``apply_locale`` call (may raise)."""
    wanted = str(requested or "").strip()
    seen = await adb.runtime_locale(serial)
    if seen.get("error"):
        detail = str(seen["error"])[:200]
        return _locale_body(LocaleFacts(wanted, "", "", LOCALE_FROM_DEVICE, detail))
    actual = str(seen.get("content") or "")
    if not wanted:
        return _locale_body(LocaleFacts("", actual, "", LOCALE_FROM_DEVICE, ""))
    source = _locale_source(wanted, booted_with)
    return await _locale_confirm(serial, _LocaleCase(wanted, actual, source))


async def _locale_changed(serial: str, case: _LocaleCase) -> dict:
    """Write *wanted* to the device and report what it says afterwards."""
    changed = await adb.set_locale(serial, case.wanted)
    if changed.get("error"):
        detail = str(changed["error"])[:200]
        return _locale_body(
            LocaleFacts(case.wanted, case.actual, "", case.source, detail)
        )
    body = changed.get("content") or {}
    return _locale_body(
        LocaleFacts(
            case.wanted,
            str(body.get("runtime") or ""),
            str(body.get("persisted") or ""),
            case.source,
            "",
        )
    )


def locale_phrase(resolved: object) -> str:
    """The language clause of the device line, or ``""``.

    Read from the RECORD, never from the device again: two readers asking the
    device could get two answers, and a finished run must still say what it ran
    in after the emulator is gone.
    """
    body = resolved if isinstance(resolved, dict) else {}
    record: Any = body.get("locale") if isinstance(body.get("locale"), dict) else {}
    actual = str(record.get("actual") or "")
    requested = str(record.get("requested") or "")
    if not actual and not requested:
        return ""
    if requested and not record.get("matched"):
        return ", NOT in " + requested + (" but in " + actual if actual else "")
    return ", in " + actual if actual else ""
