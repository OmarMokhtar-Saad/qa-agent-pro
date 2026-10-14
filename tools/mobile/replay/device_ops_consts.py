"""Replay engine: device-op constants (keyboard, password refusal, system keys)."""

from __future__ import annotations

from tools.mobile.replay.context import STATUS_NEEDS_MODEL
from tools.mobile.replay.guard_consts import NO_FABRICATION_NOTE

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

#: Ops that go through the QA keyboard, so a script carrying one needs it up.
KEYBOARD_OPS: frozenset = frozenset({"type", "fill", "clear"})

#: What a run that brought the keyboard up and put it back tells the tester.
KEYBOARD_NOTE = "QA keyboard installed and selected; previous keyboard restored."

#: Ops that are one system key press, and the keycode each sends.
_SYSTEM_KEY_OPS = {"back": "KEYCODE_BACK", "home": "KEYCODE_HOME"}
