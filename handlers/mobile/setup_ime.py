"""The QA-keyboard rows of the qa-doctor mobile section (moved from setup_check)."""

from __future__ import annotations

import asyncio
import logging

from config.settings_guard import never_raise
from handlers import _reply, mobile_gate
from tools.mobile import ime, ime_nonce, ime_session

logger = logging.getLogger("tools.mcp_handlers")


# THE QUESTION THE REST OF THE SECTION DOES NOT ASK. Every other row here is
# about whether the lane can DRIVE a device; these are about whether it can
# TYPE on one, which has a different answer -- a device can be attached, free
# and running the tester's own keyboard, in which case every row above is a
# tick and no script that types will do anything at all.
async def _mobile_ime_rows(serials: list) -> list:
    """Per-device QA-keyboard lines: installed, selected, and does it answer."""
    out: list = []
    for serial in serials:
        where = str(serial or "?")
        found = await _mobile_ime_state(ime_session, where)
        rows = _mobile_ime_static_rows(ime, where, found)
        out.extend(rows or await _mobile_ime_probe_rows(ime, where))
    return out


async def _mobile_ime_state(ime_session, where: str) -> dict:
    """The keyboard state of *where*, or an error dict when the probe fails."""
    with never_raise() as g:
        return await asyncio.wait_for(
            ime_session.state(where), timeout=mobile_gate.MOBILE_IME_STATE_S
        )
    # A timeout's str() is empty, which printed "did not finish ()".
    why = (
        "within " + str(int(mobile_gate.MOBILE_IME_STATE_S)) + "s"
        if isinstance(g.exc, asyncio.TimeoutError)
        else "(" + str(g.exc)[:80] + ")"
    )
    return {
        "error": "the keyboard probe did not finish " + why,
        "content": None,
    }


def _ime_check_failed_row(where: str, found: dict) -> str:
    # NOT "the keyboard is missing". A probe that failed says nothing
    # about the device, and this file has shipped that confusion before.
    return (
        "- ⚠️ Could not check the QA keyboard on "
        + where
        + ": "
        + str(found.get("error") or "the probe failed")[:160]
    )


def _ime_not_installed_row(where: str) -> str:
    return (
        "- ⬜ The QA keyboard is not installed on "
        + where
        + " yet, so typing is unavailable there until a run installs "
        "it; the run hands your own keyboard back when it finishes"
    )


def _ime_unpinned_row(where: str) -> str:
    # Only False: None is "could not tell" (no sha256sum, say), which a
    # run also accepts, so it is no reason to warn.
    return (
        "- ⬜ The QA keyboard on "
        + where
        + " is not the build this release ships, so the next run that "
        "types replaces it first; nothing for you to do"
    )


def _ime_inactive_row(ime, where: str, body: dict) -> str:
    return (
        "- ⬜ The QA keyboard is installed on "
        + where
        + " but `"
        + (ime.display_id(body.get("current")) or "another keyboard")
        + "` is the active one, so typing is unavailable until a run "
        "selects it; the run restores yours afterwards"
    )


def _mobile_ime_static_rows(ime, where: str, found: dict) -> list:
    """The one line for a failed probe or a keyboard that is missing,
    outdated or not selected; [] when the keyboard needs a live probe."""
    if found.get("error") or found.get("content") is None:
        return [_ime_check_failed_row(where, found)]
    body = found.get("content") or {}
    if not body.get("installed"):
        return [_ime_not_installed_row(where)]
    if body.get("pinned") is False:
        return [_ime_unpinned_row(where)]
    if not body.get("selected"):
        return [_ime_inactive_row(ime, where, body)]
    return []


# SELECTED IS NOT WORKING, and that gap is the whole reason this
# change exists: ADBKeyBoard is selected on API 35 and its receiver
# never registers, so a run types into silence and reports success.
# A keyboard that does not answer is reported as broken rather than
# assumed good because it is selected.
async def _mobile_ime_probe_rows(ime, where: str) -> list:
    """The one line for a selected keyboard, from a live test message."""
    with never_raise() as g:
        answered = await asyncio.wait_for(
            ime.probe(where), timeout=_reply.MOBILE_PROBE_S
        )
    if g.failed:
        answered = {"error": str(g.exc)[:80], "content": None}
    if answered.get("error") or not (answered.get("content") or {}).get("ok"):
        return [
            "- \u26a0\ufe0f The QA keyboard is active on "
            + where
            + " but did not answer a test message, so typing may silently "
            "do nothing: "
            + str(answered.get("error") or "no reply from the input method")[:160]
        ]
    return [_answering_row(where)]


def _answering_row(where: str) -> str:
    """The line for a keyboard that answered: green, or yellow on protocol 1."""
    if ime_nonce.protocol(where) == ime_nonce.PROTOCOL_LEGACY:
        return (
            "- \u26a0\ufe0f The QA keyboard on "
            + where
            + " answers but speaks legacy protocol 1: slow dumps, "
            "update the QA keyboard"
        )
    return "- \u2705 The QA keyboard is active on " + where + " and answering"


def _legacy_version(detail: str) -> bool:
    """True for a pinned build older than 1.1.0 (protocol 1, slow dumps)."""
    tail = detail.rpartition("@")[2].strip().split(".")
    try:
        return (int(tail[0]), int(tail[1])) < (1, 1)
    except (ValueError, IndexError):
        return False


def pinned_row(pinned: dict) -> str:
    """The pinned-keyboard line: green, or yellow for a legacy build."""
    detail = str(pinned.get("detail") or "pinned")
    if not pinned.get("ok"):
        return (
            "- \u2b1c QA input method not pinned yet, so typing into the app "
            "is unavailable; taps and assertions work"
        )
    if _legacy_version(detail):
        return (
            "- \u26a0\ufe0f QA input method "
            + detail
            + " is the legacy protocol 1: dumps are slow, update the QA keyboard"
        )
    return "- \u2705 QA input method " + detail
