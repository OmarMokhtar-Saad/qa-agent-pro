"""Replay engine: device operations (tap, type, press, scroll, keyboard, launch). Cut verbatim from tools/mobile/executor.py (P4.5)."""

from __future__ import annotations

import logging

from tools.mobile import actions as actions_mod
from tools.mobile import (
    adb,
    ime,
    launch_guard,
    step_timing,
)
from tools.mobile.replay.context import STATUS_NEEDS_MODEL, Context
from tools.mobile.replay.dump import _drop_carried
from tools.mobile.replay.elements import (
    _SWIPE,
    TOO_SMALL_TO_SWIPE,
    _center,
    _swipe_points,
)
from tools.mobile.replay.guard import NO_FABRICATION_NOTE
from tools.untrusted import wrap_untrusted

logger = logging.getLogger("tools.mobile.executor")


#: Characters of the active keyboard's package :data:`QA_IME_NOT_ACTIVE`
#: names; sized so the whole reply fits the 400-char device-error detail.
QA_IME_SEEN_CAP = 40
#: A ``type``/``clear`` while a different keyboard is active: every broadcast
#: after this goes nowhere, so nothing is attempted.
QA_IME_NOT_ACTIVE = (
    "The QA keyboard is not the active input method (active: %s), so "
    "nothing was typed. Restart the run with `qa_mobile_test`. " + NO_FABRICATION_NOTE
)


#: What a refused `type` into a password input comes back as. NEEDS_MODEL, not
#: ERROR: the refusal tells the model to ask the tester with `ask_tester` and
#: then reference the field, and an ERROR ends the case -- so the instruction
#: could not be followed by the only reader it has. A target that is not on the
#: screen, a lesser mistake, has always got a re-plan.
PASSWORD_REFUSAL_STATUS = STATUS_NEEDS_MODEL

PASSWORD_REFUSAL = (
    "That field is a password input, so nothing was typed into it. Ask the "
    "tester for the value with ask_tester, then reference that same field name "
    "from a type action with secret=true and no text -- the value is typed "
    "straight to the device and is never stored."
)


def _typer(fallback: str):
    """The typing function for this replay: the fallback one when the keyboard is down."""
    return ime.type_via_input if fallback else ime.type_text


async def _qa_ime_blocker(serial: str) -> dict | None:
    """A named blocker when the QA keyboard is not the active one, else None.

    ``keyboard_up`` selected it before the script started, but a tap can bring
    up another keyboard and every broadcast after that goes nowhere. A read
    error or a DIFFERENT keyboard stops the step; a blank read proves nothing
    and is left to the read-back verdict.
    """
    ours = str(((ime.manifest() or {}).get("content") or {}).get("ime_id") or "")
    current = await step_timing.timed("ime_check", ime.current_ime(serial))
    if current.get("error"):
        seen = "an unreadable setting: " + str(current["error"])[:120]
    else:
        seen = str(current.get("content") or "")
        if not seen or ime.same_component(seen, ours):
            return None
    # The package only, capped: the device-error detail is cut at 400
    # chars, and the note at the end is the part that must survive.
    package = seen.split("/", 1)[0][:QA_IME_SEEN_CAP]
    return {"error": QA_IME_NOT_ACTIVE % package, "content": None}


#: Ops that go through the QA keyboard, so a script carrying one needs it up.
KEYBOARD_OPS: frozenset = frozenset({"type", "fill", "clear"})

#: What a run that brought the keyboard up and put it back tells the tester.
KEYBOARD_NOTE = "QA keyboard installed and selected; previous keyboard restored."


def needs_keyboard(script: object) -> bool:
    """True when *script* carries a ``type`` or ``clear`` step."""
    return any(
        str(getattr(item, "op", "") or "") in KEYBOARD_OPS
        for item in list(getattr(script, "actions", None) or [])
    )


async def keyboard_up(serial: str) -> dict:
    """Remember the current keyboard; install, enable, select ours; QUERY once.

    The install is skipped when ``ime.installed`` says it is there, and the
    select when the current keyboard already IS ours. The one ``ime.probe`` at
    the end is the run's receiver check: every type/clear after it passes
    ``receiver_known=True`` and sends no QUERY of its own. ``content`` is
    ``{"previous": <id or "">}``; ``error`` names the step that failed, and a
    failure AFTER the select still carries ``previous`` so it is restored.
    Never raises.
    """
    _drop_carried(serial)  # the keyboard changes what is on screen
    try:
        remembered = await ime.remember_previous(serial)
        if remembered.get("error"):
            return _keyboard_failure(
                "reading the current keyboard failed: " + str(remembered["error"])
            )
        previous = str((remembered.get("content") or {}).get("previous") or "")
        failed = await _keyboard_install(serial)
        if failed is None:
            failed = await _keyboard_select(serial, previous)
        if failed is None:
            failed = await _keyboard_arm_and_probe(serial, previous)
        return failed or {"error": None, "content": {"previous": previous}}
    except Exception as exc:
        logger.exception("mobile.executor.keyboard_up failed")
        return {"error": "QA keyboard not set up: " + str(exc), "content": None}


def _keyboard_failure(reason: str, previous: str | None = None) -> dict:
    """The ``keyboard_up`` error shape; ``previous`` rides along once known."""
    content = None if previous is None else {"previous": previous}
    return {"error": "QA keyboard not set up: " + reason, "content": content}


async def _keyboard_install(serial: str) -> dict | None:
    """Install (when missing or a foreign build) and enable ours; a failure or None."""
    present = await ime.installed(serial)
    if present.get("error"):
        return _keyboard_failure(
            "checking whether it is installed failed: " + str(present["error"])
        )
    missing = not (present.get("content") or {}).get("installed")
    if missing or await ime.is_foreign_build(serial):
        put = await ime.install(serial)
        if put.get("error"):
            return _keyboard_failure("installing it failed: " + str(put["error"]))
    enabled = await ime.enable(serial)
    if enabled.get("error"):
        return _keyboard_failure("enabling it failed: " + str(enabled["error"]))
    return None


async def _keyboard_select(serial: str, previous: str) -> dict | None:
    """Select ours unless it is already the current keyboard; a failure or None."""
    ours = str(((ime.manifest() or {}).get("content") or {}).get("ime_id") or "")
    current = await step_timing.timed("ime_check", ime.current_ime(serial))
    if current.get("error") or not ime.same_component(current.get("content"), ours):
        chosen = await ime.select(serial)
        if chosen.get("error"):
            return _keyboard_failure(
                "selecting it failed: " + str(chosen["error"]), previous
            )
    return None


async def _keyboard_arm_and_probe(serial: str, previous: str) -> dict | None:
    """G4: arm before the receiver check. A refused or unexpected ARM stops the
    keyboard here, which `replay` turns into the usual typing fallback and notice.
    """
    armed = await ime.arm(serial)
    if armed.get("error"):
        return _keyboard_failure(str(armed["error"]), previous)
    probed = await ime.probe(serial)
    if probed.get("error") or not (probed.get("content") or {}).get("ok"):
        return _keyboard_failure(
            "it does not answer its status query: "
            + str(probed.get("error") or "no result= in the reply"),
            previous,
        )
    return None


async def keyboard_down(serial: str, previous: str) -> dict:
    """Put the remembered keyboard back. ``content.note`` is what to tell."""
    _drop_carried(serial)  # the keyboard changes what is on screen
    try:
        restored = await ime.restore_previous(serial, previous)
        if restored.get("error"):
            return {
                "error": "QA keyboard installed and selected; previous keyboard "
                "NOT restored: " + str(restored["error"]),
                "content": None,
            }
        return {"error": None, "content": {"note": KEYBOARD_NOTE}}
    except Exception as exc:
        logger.exception("mobile.executor.keyboard_down failed")
        return {
            "error": "QA keyboard installed and selected; previous keyboard "
            "NOT restored: " + str(exc),
            "content": None,
        }


#: Ops that are one system key press, and the keycode each sends.
_SYSTEM_KEY_OPS = {"back": "KEYCODE_BACK", "home": "KEYCODE_HOME"}


async def _tap_op(element: object, serial: str) -> dict:
    """``tap`` / ``tap_text``: tap the centre of the chosen element."""
    # ONE device path for both. `tap_text` differs only in how its element
    # was chosen; once chosen a tap is a tap, and a second `adb.tap` call
    # site would be a second place for the bounds check to drift.
    center = _center(element) if isinstance(element, dict) else None
    if not center:
        return {"error": "That element has no usable bounds.", "content": None}
    return await adb.tap(serial, center[0], center[1])


async def _perform(
    op: str,
    action: object,
    element: object,
    ctx: Context,
    display: object = None,
) -> dict:
    """One device op. Every argv is built by ``adb``, which validates again."""
    serial = ctx.serial
    # Any device op can change the screen, so a carried read is stale from here.
    _drop_carried(serial)
    if op in _SYSTEM_KEY_OPS:
        return await adb.keyevent(serial, _SYSTEM_KEY_OPS[op])
    if op == "launch":
        return await _launch_op(action, ctx)
    if op == "clear_app_data":
        return await _clear_app_data_op(ctx)
    if op == "open_url":
        return await adb.open_url(serial, str(getattr(action, "url", "")))
    if op in actions_mod.WAIT_OPS:
        # Every wait is handled, and polled, by `replay` before any device op;
        # a wait reaching here would otherwise fall through to the unknown op.
        return {"error": "A wait is not a device op.", "content": None}
    if op == "scroll":
        return await _scroll_op(action, element, display, serial)
    if op in ("tap", "tap_text"):
        return await _tap_op(element, serial)
    if op == "press":
        return await _press_op(action, element, serial)
    if op in ("type", "fill", "clear"):
        return await _text_entry_op(op, action, element, ctx)
    return {"error": "Unsupported action " + repr(op), "content": None}


async def _launch_op(action: object, ctx: Context) -> dict:
    """``launch``: start a named package (guarded) or this run's own package."""
    serial = ctx.serial
    named = getattr(action, "package", None)
    if named:
        # A named package is judged by launch_guard BEFORE anything is
        # started; a refusal is recoverable (the model fixes the step).
        # `launch_allowed` is reached only when a package is set.
        allowed, why = launch_guard.launch_allowed(
            named,
            target=ctx.package,
            expect_apps=(
                *tuple(getattr(ctx, "expect_apps", ()) or ()),
                getattr(ctx, "home_package", ""),
            ),
        )
        if not allowed:
            return {"error": why, "content": None, "needs_model": True}
        return await adb.launch(serial, named)
    if not ctx.package:
        return {"error": "No app package is set for this run.", "content": None}
    return await adb.launch(serial, ctx.package)


async def _clear_app_data_op(ctx: Context) -> dict:
    """``clear_app_data``: wipe this run's own package, then relaunch it."""
    serial = ctx.serial
    # Only ever THIS run's own package -- `ctx.home_package` (falling back
    # to `ctx.package` for a Context built without one), never a value
    # read off the script, and never the app a multi-app goal handed over
    # to: a hand-over moves what the guard compares against, not what may
    # be wiped.
    home = getattr(ctx, "home_package", "") or ctx.package
    if not home:
        return {"error": "No app package is set for this run.", "content": None}
    cleared = await adb.clear_app_data(serial, home)
    if cleared.get("error"):
        return cleared
    return await adb.launch(serial, home)


async def _scroll_op(
    action: object, element: object, display: object, serial: str
) -> dict:
    """``scroll``: swipe in the named direction, within the target if any."""
    direction = str(getattr(action, "dir", "") or "")
    if direction not in _SWIPE:
        return {"error": "Unknown scroll direction.", "content": None}
    points = _swipe_points(direction, element, display)
    if not points:
        return {
            "error": TOO_SMALL_TO_SWIPE,
            "content": None,
            # A re-plan, not a device failure: the model can drop the
            # target or name the scrollable list instead.
            "needs_model": True,
        }
    return await adb.swipe(serial, *points)


async def _press_op(action: object, element: object, serial: str) -> dict:
    """``press``: focus the target by a tap, then send the allowlisted key."""
    # The key goes to whatever holds FOCUS, so the target is not decorative:
    # tap it first, exactly as `type` and `clear` do. The keycode comes from
    # `actions.PRESS_KEYS` -- our own allowlist, never the model's string --
    # and `adb.keyevent` validates it a second time.
    code = actions_mod.PRESS_KEYS.get(str(getattr(action, "key", "") or ""))
    if not code:
        return {"error": "Unsupported press key.", "content": None}
    center = _center(element) if isinstance(element, dict) else None
    if not center:
        return {"error": "That field has no usable bounds.", "content": None}
    focused = await adb.tap(serial, center[0], center[1])
    if focused.get("error"):
        return focused
    return await adb.keyevent(serial, code)


def _password_refusal(action: object, element: object) -> dict | None:
    """The refusal for typing a non-secret value into a password field, else None."""
    # A PASSWORD INPUT TAKES ONLY A TESTER-SUPPLIED VALUE. Refused by name
    # rather than typed and masked afterwards, and decided on the ELEMENT
    # rather than on the action's chosen names: a field named in an alphabet
    # this server cannot read is exactly the case a name-matching rule
    # cannot judge, and it is also the case where the dump still says
    # `password="true"`.
    secret = bool(getattr(action, "secret", False))
    if (
        not secret
        and isinstance(element, dict)
        and (element.get("secure") or element.get("role") == "password")
    ):
        return {
            "error": PASSWORD_REFUSAL,
            "content": None,
            # NEEDS_MODEL rather than the device_error every other `_perform`
            # failure becomes: this one names a recovery, and an error ends
            # the case before the model can take it.
            "needs_model": True,
            # REFUSING TO TYPE IT IS NOT ENOUGH. The trace entry was built
            # before the element was known, so it still carries the literal
            # -- and the trace is checkpointed, rendered and audited. This
            # asks the caller to mask it, which is the only place that can:
            # `redact_action` sees the action alone and this value is a
            # credential because of the ELEMENT it was aimed at.
            "mask_text": True,
        }
    return None


async def _text_entry_op(
    op: str, action: object, element: object, ctx: Context
) -> dict:
    """``type`` / ``fill`` / ``clear``: focus the field, then enter or clear text."""
    serial = ctx.serial
    fallback = str(getattr(ctx, "typing_fallback", "") or "")
    if fallback and op == "clear":
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
    blocked = None if fallback else await _qa_ime_blocker(serial)
    if blocked is not None:
        return blocked
    center = _center(element) if isinstance(element, dict) else None
    if not center:
        return {"error": "That field has no usable bounds.", "content": None}
    focused = await adb.tap(serial, center[0], center[1])
    if focused.get("error"):
        return focused
    if op == "clear":
        # `replay` ran keyboard_up (its one QUERY) for every script
        # carrying type/clear, so the receiver is known to answer.
        return await ime.clear(serial, receiver_known=True)
    refused = _password_refusal(action, element)
    if refused is not None:
        return refused
    if bool(getattr(action, "secret", False)):
        return await _type_secret(action, ctx, fallback)
    return await _type_plain(action, serial, fallback)


async def _type_secret(action: object, ctx: Context, fallback: str) -> dict:
    """Type the tester-held value for a ``secret`` field, or say none is held."""
    serial = ctx.serial
    field = str(getattr(action, "field", "") or "")
    value = (ctx.tester_inputs or {}).get(field)
    # `not value`, not `value is None`: an EMPTY held value types
    # nothing, and the `landed` verdict cannot speak for a step that
    # made no claim, so it would pass silently -- the defect that
    # verdict exists to end. `actions.parse_script` already refuses an
    # empty `text` on the non-secret path ("type needs text"); this is
    # the same boundary for the path whose value never appears in the
    # script.
    if not value:
        return {
            "error": (
                "No tester-supplied value is held for the field "
                + repr(field[:60])
                + ", so nothing was typed. Ask for it first with "
                "ask_tester."
            ),
            "content": None,
        }
    return _judge_landed(
        await _typer(fallback)(serial, str(value), secret=True, receiver_known=True),
        field or "the focused field",
    )


async def _type_plain(action: object, serial: str, fallback: str) -> dict:
    """Type the script's own text and judge whether it landed."""
    return _judge_landed(
        await _typer(fallback)(
            serial,
            str(getattr(action, "text", "") or ""),
            secret=False,
            receiver_known=True,
        ),
        str(getattr(action, "field", "") or "") or "the focused field",
    )


def _judge_landed(result: object, field: str) -> dict:
    """A typed step whose text did not land is NOT a success.

    ``ime.type_text`` reports two different things and only one of them used
    to be read: ``typed`` is what the HOST SENT, ``landed`` is what the DEVICE
    ACCEPTED. Returning the dict unchanged meant a field that took nothing --
    no input connection, a read-only view, a WebView that ignored the commit --
    produced a success-shaped step, and the run PASSED having typed nothing.

    Two non-success shapes, because the two verdicts need different answers:

    * ``no`` -- the field is byte-identical to before the commit. That is a
      device_error like any other failed ``_perform``: it ends the case.
    * ``unknown`` -- the keyboard could not say (no input connection, no ``t:``
      part, or a formatter transformed the text). This takes the
      ``needs_model`` arm the password refusal already uses, because it names a
      RECOVERY -- look at the screenshot and confirm -- rather than asserting a
      failure nobody measured. It is still not a success: an unconfirmed type
      never passes silently.
    """
    if not isinstance(result, dict):
        # Unreachable: `ime.type_text` returns a dict on every path. A FIXED
        # string, never `str(result)`: that object might be a field's contents.
        return {"error": "The keyboard returned an unusable reply.", "content": None}
    if result.get("error"):
        return result
    content = result.get("content") or {}
    landed = content.get("landed")
    if landed == ime.LANDED_YES:
        return result
    # No `if not typed: return result` guard. It would be unreachable on the
    # script path (`parse_script` refuses `type` with no text) and WRONG on
    # the secret path, where an empty held value would then pass silently;
    # that case is refused above, at the boundary, by name.
    typed = content.get("typed", 0)
    if landed == ime.LANDED_NO:
        return {
            "error": (
                "Typed "
                + str(typed)
                + " character(s) into "
                + repr(field[:60])
                + " but the field did not take it, so the step did not happen. "
                + NO_FABRICATION_NOTE
            ),
            "content": None,
        }
    return _unconfirmed_type_reply(bool(content.get("secret")), typed, field)


def _unconfirmed_type_reply(secret: bool, typed: object, field: str) -> dict:
    """The ``needs_model`` reply for a type the keyboard could not confirm."""
    # TWO recoveries, because the two modes can be asked for different things.
    # Telling the model to check that "the field shows the value" is the right
    # instruction for ordinary text and exactly the wrong one for a
    # credential: the field is masked, so it cannot be done, and asking for it
    # points a model at a plaintext password on a screen capture -- undoing at
    # the prose layer what the length-only comparison protects in the code.
    if secret:
        recovery = (
            " and the keyboard could not confirm it landed. Look at the "
            "screenshot: the field is masked, so check that it is non-empty "
            "and roughly the right length. Never read the value back."
        )
    else:
        recovery = (
            " and the keyboard could not confirm the text landed. Look at the "
            "screenshot: if the field shows the value (a formatter may have "
            "reshaped it) continue, otherwise the step did not happen."
        )
    return {
        "error": (
            "Typed " + str(typed) + " character(s) into " + repr(field[:60]) + recovery
        ),
        "content": None,
        "needs_model": True,
    }
