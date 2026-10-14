"""Hand the tester's keyboard back, and read back what was typed."""

from __future__ import annotations

import asyncio
import logging

from tools.mobile import ime, imes_record

logger = logging.getLogger("tools.mobile.ime_session")

#: How long the read-back after a type op may take.
#:
#: One broadcast and one reply from an app already installed and selected. The
#: constraint is that this runs after EVERY type op, so it is paid per op rather
#: than per run and must not become the thing that makes typing feel slow.
IME_VERIFY_TIMEOUT_S = 15.0


def _noop(detail: str) -> dict:
    """A reported no-op: nothing was restored, and the tester is told why."""
    return {"error": None, "content": {"restored": False, "detail": detail}}


async def _give_back(run_id: str, record: dict) -> dict:
    """Restore the keyboard *record* names, then drop the record."""
    serial = str(record.get("serial") or "")
    previous = str(record.get("previous") or "")
    if not serial:
        imes_record.clear_record(run_id)
        return _noop("the run's IME record named no device, so nothing was restored")
    result = await ime.restore_previous(serial, previous)
    # The record is dropped either way: a restore that failed will not
    # succeed on a second attempt from a run that has already ended, and a
    # stale record would make the NEXT run think it had already remembered.
    imes_record.clear_record(run_id)
    if result.get("error"):
        return result
    return result


# Give the tester's keyboard back. Takes NO serial -- it reads its own record.
#
# That is what lets one chokepoint call it: ``session.finish_device`` knows the
# run's owner and nothing else, and a restore that demanded a serial would push
# the job of remembering one onto every caller.
#
# A no-op is REPORTED, never silent, and there are four of them: no record (the
# run never typed), a run folder that is gone, a blank previous, and a previous
# that is our own keyboard. A tester cannot otherwise tell "nothing needed
# doing" from "the restore failed quietly".
async def restore(run_id: str) -> dict:
    """Give the tester's keyboard back. Takes NO serial -- it reads its own record."""
    try:
        record = imes_record.read_record(run_id)
        if not record:
            return _noop(
                "No input method was changed for this run, so none was restored."
            )
        return await _give_back(run_id, record)
    except Exception as exc:
        logger.exception("mobile.ime_session.restore failed")
        return {"error": str(exc), "content": None}


def _read_timeout(serial: str) -> dict:
    """The refusal when the read-back ran out of time."""
    return {
        "error": (
            "Could not read the field back from "
            + str(serial)[:64]
            + " within "
            + str(int(IME_VERIFY_TIMEOUT_S))
            + "s, so what was typed could not be verified."
        ),
        "content": None,
    }


async def read_back(serial: str) -> dict:
    """The field's text after a type op, within a bound.

    Separate from ``ime.query`` so the cap has an owner: this runs after EVERY
    type op, so an unbounded read would make one unresponsive device stall a
    whole run one op at a time. A timeout is "could not verify", never "the text
    matched" -- a verification that fails open verifies nothing.
    """
    try:
        return await asyncio.wait_for(ime.query(serial), timeout=IME_VERIFY_TIMEOUT_S)
    except asyncio.TimeoutError:
        return _read_timeout(serial)


def _mismatch(got: str) -> dict:
    """The not-verified answer when the field does not end with the sent text."""
    return {
        "ok": False,
        "detail": (
            "the field does not end with the text that was sent -- it holds "
            + (repr(got[-120:]) if got else "nothing")
            + ". The keyboard may not be the active one."
        ),
    }


def verify_typed(field_text: object, sent: object) -> dict:
    """Did the field end up holding what was typed?

    **Ends-with, not equality.** A field may already hold text -- typing into a
    non-empty field is an append, and equality would fail every one of them.
    What this can honestly assert is that what was sent is now at the end.

    Returns ``{"ok", "detail"}``. A blank *sent* is vacuous and is reported as
    not verified rather than as a pass, because "we asked for nothing and found
    nothing" is not evidence the keyboard works.
    """
    wanted = str(sent or "")
    got = str(field_text or "")
    if not wanted:
        return {"ok": False, "detail": "nothing was sent, so nothing was verified"}
    if got.endswith(wanted):
        return {"ok": True, "detail": "the field ends with the text that was sent"}
    return _mismatch(got)
