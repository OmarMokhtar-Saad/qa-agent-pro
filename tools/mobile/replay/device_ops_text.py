"""Replay engine: the text-entry ops (type, fill, clear) and the password refusal."""

from __future__ import annotations

from tools.mobile import adb, ime
from tools.mobile.replay.context import Context
from tools.mobile.replay.device_ops_consts import PASSWORD_REFUSAL
from tools.mobile.replay.device_ops_ime import _qa_ime_blocker, _typer
from tools.mobile.replay.device_ops_judge import _judge_landed
from tools.mobile.replay.elements import _center
from tools.mobile.replay.elements_swipe import TouchSpec
from tools.untrusted import wrap_untrusted


# In the refusal below, `needs_model`: NEEDS_MODEL rather than the device_error
# every other `_perform` failure becomes: this one names a recovery, and an
# error ends the case before the model can take it.
# And `mask_text`: REFUSING TO TYPE IT IS NOT ENOUGH. The trace entry was built
# before the element was known, so it still carries the literal
# -- and the trace is checkpointed, rendered and audited. This
# asks the caller to mask it, which is the only place that can:
# `redact_action` sees the action alone and this value is a
# credential because of the ELEMENT it was aimed at.
#
# A PASSWORD INPUT TAKES ONLY A TESTER-SUPPLIED VALUE. Refused by name
# rather than typed and masked afterwards, and decided on the ELEMENT
# rather than on the action's chosen names: a field named in an alphabet
# this server cannot read is exactly the case a name-matching rule
# cannot judge, and it is also the case where the dump still says
# `password="true"`.
def _password_refusal(action: object, element: object) -> dict | None:
    """The refusal for typing a non-secret value into a password field, else None."""
    secret = bool(getattr(action, "secret", False))
    if (
        not secret
        and isinstance(element, dict)
        and (element.get("secure") or element.get("role") == "password")
    ):
        return {
            "error": PASSWORD_REFUSAL,
            "content": None,
            "needs_model": True,
            "mask_text": True,
        }
    return None


def _clear_refusal(fallback: str) -> dict:
    """The refusal for ``clear`` while the QA keyboard is not set up."""
    return {
        "error": (
            "Clearing a field needs the QA keyboard, which is not set up on this "
            "device. Clear it by hand, or install the keyboard as the note "
            "about the QA keyboard says. Reason:\n"
            + wrap_untrusted("adb", fallback, limit=200)
        ),
        "content": None,
        "needs_model": True,
    }


async def _focus_field(
    serial: str, element: object, force: bool = False
) -> dict | None:
    """Tap the field to focus it; an error reply, or None once it is focused.

    *force* taps even when the tree shows the field focused: a ``clear`` re-renders
    some fields (a search bar re-filters) and leaves the flag set while the keyboard's
    input connection is stale, so the text typed next is dropped (M-03).
    """
    if not force and isinstance(element, dict) and element.get("focused") is True:
        return None  # the tree already shows it focused: a tap adds a round trip
    center = _center(element) if isinstance(element, dict) else None
    if not center:
        return {"error": "That field has no usable bounds.", "content": None}
    focused = await adb.tap(serial, center[0], center[1])
    return focused if focused.get("error") else None


# Serials whose last text-entry op was a ``clear``. Kept here, not on the per-call
# Context, so a ``clear`` in one submit still re-focuses the ``type`` of the next.
_CLEARED: set[str] = set()


def _take_cleared(serial: str, op: str) -> bool:
    """Whether the step before cleared a field; consumed by a ``type`` / ``fill``."""
    if op == "clear":
        return False
    cleared = serial in _CLEARED
    _CLEARED.discard(serial)
    return cleared


async def _clear_field(serial: str) -> dict:
    """Clear the focused field and remember it, so the next type re-focuses it."""
    reply = await ime.clear(serial, receiver_known=True)
    if reply.get("error"):
        _CLEARED.discard(serial)
    else:
        _CLEARED.add(serial)
    return reply


# For `clear`: `replay` ran keyboard_up (its one QUERY) for every script
# carrying type/clear, so the receiver is known to answer.
async def _text_entry_op(spec: TouchSpec, ctx: Context) -> dict:
    """``type`` / ``fill`` / ``clear``: focus the field, then enter or clear text."""
    serial = ctx.serial
    fallback = str(getattr(ctx, "typing_fallback", "") or "")
    if fallback and spec.op == "clear":
        return _clear_refusal(fallback)
    blocked = None if fallback else await _qa_ime_blocker(serial)
    if blocked is not None:
        return blocked
    failed = await _focus_field(serial, spec.element, _take_cleared(serial, spec.op))
    if failed is not None:
        return failed
    if spec.op == "clear":
        return await _clear_field(serial)
    refused = _password_refusal(spec.action, spec.element)
    if refused is not None:
        return refused
    if bool(getattr(spec.action, "secret", False)):
        return await _type_secret(spec.action, ctx, fallback)
    return await _type_plain(spec.action, serial, fallback)


# `not value`, not `value is None`: an EMPTY held value types
# nothing, and the `landed` verdict cannot speak for a step that
# made no claim, so it would pass silently -- the defect that
# verdict exists to end. `actions.parse_script` already refuses an
# empty `text` on the non-secret path ("type needs text"); this is
# the same boundary for the path whose value never appears in the
# script.
async def _type_secret(action: object, ctx: Context, fallback: str) -> dict:
    """Type the tester-held value for a ``secret`` field, or say none is held."""
    field = str(getattr(action, "field", "") or "")
    value = (ctx.tester_inputs or {}).get(field)
    if not value:
        return _no_secret_held(field)
    return _judge_landed(
        await _typer(fallback)(
            ctx.serial, str(value), ime.TypeOpts(secret=True, receiver_known=True)
        ),
        field or "the focused field",
    )


def _no_secret_held(field: str) -> dict:
    """The reply for a ``secret`` field with no tester-supplied value held."""
    return {
        "error": (
            "No tester-supplied value is held for the field "
            + repr(field[:60])
            + ", so nothing was typed. Ask for it first with "
            "ask_tester."
        ),
        "content": None,
    }


async def _type_plain(action: object, serial: str, fallback: str) -> dict:
    """Type the script's own text and judge whether it landed."""
    return _judge_landed(
        await _typer(fallback)(
            serial,
            str(getattr(action, "text", "") or ""),
            ime.TypeOpts(secret=False, receiver_known=True),
        ),
        str(getattr(action, "field", "") or "") or "the focused field",
    )
