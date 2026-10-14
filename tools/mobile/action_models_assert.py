"""The assert, ask_tester and done action models."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field, model_validator

from tools.mobile.action_models_basic import Target, _Base


# ADOPT `target.text` as the operand rather than reword the refusal.
# `target: {"text": ...}` is the shape EVERY OTHER op uses to name
# something on screen, so a model reasonably reaches for it here too
# -- and a live run did: mrun-20260905-051728 was refused with
# "text_present requires text", spent a turn, and retried with the
# bare field. Rewording removes that one instance; widening removes
# the class.
#
# Normalised HERE, at the model, and that placement is the whole
# design: `executor._evaluate_assert` reads `.text` and nothing else
# for these two kinds, so it sees the canonical shape and needs no
# change. There is no second site to keep in step, and therefore no
# half-wired consumer. `target` is deliberately LEFT in place: the
# executor already resolves a target on an assert and already falls
# through when it does not match (`op not in ("scroll", "assert")`),
# so leaving it keeps the shipped path byte-for-byte.
#
# Both fields cap at 200, so an adopted operand cannot overflow the
# field it lands in.
def _adopt_text_operand(action: "AssertAction") -> None:
    if not action.text.strip() and action.target is not None:
        action.text = action.target.text
    if not action.text.strip():
        raise ValueError(
            action.kind + " needs the text to look for, in `text` or in `target.text`"
        )


class AssertAction(_Base):
    """Check something about the screen. Five kinds, and one of them is weak.

    ``new_text`` is the kind to reach for when a case says "the app replies":
    it passes only when text is on the screen that was NOT on the previous one,
    which is what an answer arriving looks like. ``contains`` narrows it to a
    reply mentioning a particular string.

    ``screen_changed`` is kept and is WEAK on purpose: it compares screen IDs,
    so ANY navigation satisfies it and it is never evidence that an answer
    arrived. The 2026-09-04 live run asserted it after each send and read the
    result as "the assistant replied"; it was not.

    THE OPERAND. For ``text_present`` and ``text_absent``, the string to look
    for may be given EITHER as ``text`` OR as ``target.text`` -- that is,
    ``target: {"text": "..."}``, the same shape every other op uses to name
    something on screen. Whichever you send, the check is identical; if you
    send both, ``text`` wins. Sending neither is refused.

    ``target.text`` is spelled here exactly as the REFUSAL spells it, and that
    is deliberate rather than cosmetic. This docstring IS the schema the model
    is handed, so prose that describes the rule without ever naming the key
    leaves the model guessing the spelling -- which is the position that
    produced the original defect. One name, spelled one way, in the schema and
    in the error.

    A ``target`` carrying no selector at all is refused one layer lower, by
    ``Target`` itself, and never reaches this rule.
    """

    op: Literal["assert"]
    kind: Literal[
        "text_present", "text_absent", "element", "new_text", "screen_changed", "visual"
    ]
    text: str = Field(default="", max_length=200)
    contains: str = Field(default="", max_length=200)
    target: Optional[Target] = None
    # What to judge from the picture, for ``visual`` only. The server cannot
    # judge it: the replay stops here and the next reply carries the screenshot.
    note: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def kind_has_its_operand(self) -> "AssertAction":
        if self.kind in ("text_present", "text_absent"):
            _adopt_text_operand(self)
        if self.kind == "element" and self.target is None:
            raise ValueError("assert element needs a target")
        if self.kind == "visual" and len(self.note.strip()) < 3:
            raise ValueError(
                "assert visual needs a `note` saying what to judge from the screenshot"
            )
        return self


# Field names that would carry a VALUE rather than a request for one. Checked
# against ``AskTesterAction``'s own field set at validation time -- see
# ``carries_no_value_field``.
_VALUE_FIELD_NAMES = frozenset(
    {"text", "value", "password", "secret_value", "input", "tester_input"}
)


class AskTesterAction(_Base):
    """Ask the tester for one field's value. The value is NEVER stored.

    This action is a REQUEST, and it must stay one. ``extra="forbid"`` already
    stops a model sending an unexpected key, but it says nothing about a future
    EDIT to this class adding a value-bearing field -- which is exactly the
    mistake ``TypeAction`` was written to prevent, and the reason it enforces
    forbid-text/require-field rather than trusting convention.

    So the invariant is enforced rather than conventional:
    ``carries_no_value_field`` inspects this class's OWN field set on every
    validation, and adding ``text`` here would make every ``ask_tester`` script
    fail loudly and immediately instead of quietly opening a channel for a
    credential to travel through a packet.
    """

    op: Literal["ask_tester"]
    prompt: str = Field(min_length=3, max_length=300)
    field: str = Field(min_length=1, max_length=80)
    secret: bool = True

    @model_validator(mode="after")
    def carries_no_value_field(self) -> "AskTesterAction":
        leaked = sorted(_VALUE_FIELD_NAMES & set(type(self).model_fields))
        if leaked:
            raise ValueError(
                "ask_tester asks for a value and must never carry one; this "
                "class has grown the field(s) " + ", ".join(leaked)
            )
        return self


class DoneAction(_Base):
    op: Literal["done"]
    verdict: Literal["pass", "fail", "blocked"]
    reason: str = Field(min_length=3, max_length=600)
