"""Type text through the IME and report whether it landed."""

from __future__ import annotations

import base64
import logging
from typing import NamedTuple

from tools.mobile.ime_consts import LANDED_NO, LANDED_UNKNOWN, MAX_TEXT_CHARS, TypeOpts
from tools.mobile.ime_host import host
from tools.mobile.ime_landed import Seen, _landed_verdict
from tools.mobile.ime_query import (
    _field_snapshot,
    _focused_field,
    _undelivered,
    last_field,
)

logger = logging.getLogger("tools.mobile.ime")


class _TypeJob(NamedTuple):
    """What one commit needs: the device, its actions and how to treat it."""

    serial: str
    actions: dict
    secret: bool
    receiver_known: bool


# ``secret=True`` changes NOTHING about the transport -- the payload always
# travels on stdin -- and changes everything about what is said about it: the
# return value never echoes the text, and the log line records only a length.
# Keeping one transport for both means the secret path is the path every test
# exercises, rather than a rarely-taken branch.
async def type_text(serial: str, text: str, opts: TypeOpts = TypeOpts()) -> dict:
    """Commit *text* into the focused field through the IME."""
    try:
        value = "" if text is None else str(text)
        too_long = _length_refusal(value)
        if too_long:
            return too_long
        resolved = host().manifest()
        if resolved.get("error"):
            return resolved
        actions = (resolved["content"] or {})["actions"]
        job = _TypeJob(serial, actions, opts.secret, opts.receiver_known)
        return await _commit_and_verify(job, value)
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


# `receiver_known`: the caller (replay.replay.replay, via keyboard_up) has
# already had the receiver answer once this run, so no per-type QUERY.
# Encoded BEFORE any broadcast, so an encoding failure is reported as
# itself rather than hidden behind a device round trip.
# BEFORE the commit, because the verdict is a DIFFERENCE. A snapshot
# taken only afterwards cannot tell a field that accepted the text
# from one that already held it.
async def _commit_and_verify(job: _TypeJob, value: str) -> dict:
    """Send *value* to the keyboard and report what the field made of it."""
    payload = base64.b64encode(value.encode("utf-8")).decode("ascii")
    if not job.receiver_known:
        refused = await _focused_field(job.serial, job.actions)
        if refused:
            return {"error": refused, "content": None}
    before = await _field_snapshot(job.serial, job.actions, job.secret)
    sent = await host().broadcast(job.serial, str(job.actions["input"]), payload)
    if sent.get("error"):
        return sent
    refused = await _undelivered(job.serial, job.actions, sent)
    if refused:
        return {"error": refused, "content": None}
    return await _verified_result(job, value, before)


async def _verified_result(job: _TypeJob, value: str, before: str | None) -> dict:
    """Read the field again and build the success envelope."""
    prior = last_field(job.serial)
    after = await _field_snapshot(job.serial, job.actions, job.secret)
    landed = _landed_verdict(Seen(before, after), value, job.secret)
    # A different field in focus than before an EMPTY read: the screen moved on
    # (an auto-submitting OTP), which is not "the field took nothing".
    if landed == LANDED_NO and prior and last_field(job.serial) != prior:
        landed = LANDED_UNKNOWN
    result = _typed_result(value, job.secret, landed)
    # EMPTY after the commit: only the screen can say whether that is a dead
    # field or an auto-submitting OTP that moved on, so the replay asks it.
    result["content"]["empty_after"] = after is not None and not after
    return result


def _typed_result(value: str, secret: bool, landed: str) -> dict:
    """The success envelope; logs only a length."""
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
