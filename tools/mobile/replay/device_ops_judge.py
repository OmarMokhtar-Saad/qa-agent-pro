"""Replay engine: judging whether typed text landed in the field."""

from __future__ import annotations

from tools.mobile import ime
from tools.mobile.replay.guard_consts import NO_FABRICATION_NOTE


# A typed step whose text did not land is NOT a success.
#
# ``ime.type_text`` reports two different things and only one of them used
# to be read: ``typed`` is what the HOST SENT, ``landed`` is what the DEVICE
# ACCEPTED. Returning the dict unchanged meant a field that took nothing --
# no input connection, a read-only view, a WebView that ignored the commit --
# produced a success-shaped step, and the run PASSED having typed nothing.
#
# Two non-success shapes, because the two verdicts need different answers:
#
# * ``no`` -- the field is byte-identical to before the commit. That is a
#   device_error like any other failed ``_perform``: it ends the case.
# * ``unknown`` -- the keyboard could not say (no input connection, no ``t:``
#   part, or a formatter transformed the text). This takes the
#   ``needs_model`` arm the password refusal already uses, because it names a
#   RECOVERY -- look at the screenshot and confirm -- rather than asserting a
#   failure nobody measured. It is still not a success: an unconfirmed type
#   never passes silently.
#
# No `if not typed: return result` guard. It would be unreachable on the
# script path (`parse_script` refuses `type` with no text) and WRONG on
# the secret path, where an empty held value would then pass silently;
# that case is refused above, at the boundary, by name.
def _judge_landed(result: object, field: str) -> dict:
    """A typed step whose text did not land is NOT a success."""
    if not isinstance(result, dict):
        # Unreachable: `ime.type_text` returns a dict on every path. A FIXED
        # string, never `str(result)`: that object might be a field's contents.
        return {"error": "The keyboard returned an unusable reply.", "content": None}
    if result.get("error"):
        return result
    content = result.get("content") or {}
    landed = content.get("landed")
    if landed == ime.LANDED_YES:
        return _with_proof(result, content, field)
    typed = content.get("typed", 0)
    if landed == ime.LANDED_NO:
        return _landed_no_with_fallback(content, field)
    return _unknown_with_advance(bool(content.get("secret")), typed, field)


# The proof a fill leaves: WHICH field took the text and a masked length check.
#
# A password or OTP field reads back as bullets (one per character), so the
# count proves the secret landed without revealing it. ``N/N chars`` is the
# characters the field holds over the characters sent; an unreadable field says
# so instead of claiming a match.
def _with_proof(result: dict, content: dict, field: str) -> dict:
    """*result* with the receiving field's name and an ``N/N chars`` check."""
    sent = content.get("typed", 0)
    seen = content.get("seen_chars")
    proof = "%s/%s chars" % (seen, sent)
    if seen is None:
        proof = "?/%s chars (field not readable)" % sent
    return {**result, "content": {**content, "field": field[:60], "chars": proof}}


def _unknown_with_advance(secret: bool, typed: object, field: str) -> dict:
    """The unconfirmed reply; the replay downgrades it to landed when the screen moved on.

    An auto-submitting field (OTP) leaves the keyboard unable to say; whether the
    screen advanced is checked by the replay, for any app.
    """
    reply = _unconfirmed_type_reply(secret, typed, field)
    reply["if_screen_changed"] = _unconfirmed_type_reply(secret, typed, field)
    reply["masked"] = secret
    return reply


def _landed_no_with_fallback(content: dict, field: str) -> dict:
    """The "did not land" reply; an EMPTY field also carries the unknown reply."""
    typed = content.get("typed", 0)
    reply = _landed_no_reply(typed, field)
    if content.get("empty_after"):
        secret = bool(content.get("secret"))
        reply["if_screen_changed"] = _unconfirmed_type_reply(secret, typed, field)
        reply["masked"] = secret
    return reply


_RETRY_REMEDY = (
    "To retry, tap the field first (a field that was just cleared can keep a "
    "stale focus), then type again. "
)


def _landed_no_reply(typed: object, field: str) -> dict:
    """The device_error reply for a field that took none of the typed text."""
    return {
        "error": (
            "Typed "
            + str(typed)
            + " character(s) into "
            + repr(field[:60])
            + " but the field did not take it, so the step did not happen. "
            + _RETRY_REMEDY
            + NO_FABRICATION_NOTE
        ),
        "content": None,
    }


# TWO recoveries, because the two modes can be asked for different things.
# Telling the model to check that "the field shows the value" is the right
# instruction for ordinary text and exactly the wrong one for a
# credential: the field is masked, so it cannot be done, and asking for it
# points a model at a plaintext password on a screen capture -- undoing at
# the prose layer what the length-only comparison protects in the code.
def _recovery_text(secret: bool) -> str:
    """What the model is asked to check next, by whether the field is masked."""
    if secret:
        return (
            " and could not read it back: the field is masked, so the keyboard cannot "
            "confirm it landed. Look at the screenshot and check that it is non-empty "
            "and roughly the right length. Never read the value back."
        )
    return (
        " and the keyboard could not confirm the text landed. Look at the "
        "screenshot: if the field shows the value (a formatter may have "
        "reshaped it) continue, otherwise the step did not happen."
    )


def _unconfirmed_type_reply(secret: bool, typed: object, field: str) -> dict:
    """The ``needs_model`` reply for a type the keyboard could not confirm."""
    recovery = _recovery_text(secret)
    return {
        "error": (
            "Typed " + str(typed) + " character(s) into " + repr(field[:60]) + recovery
        ),
        "content": None,
        "needs_model": True,
    }
