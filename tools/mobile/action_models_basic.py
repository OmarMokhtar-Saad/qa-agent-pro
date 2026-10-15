"""The basic action models: Target, tap, type, fill, clear, back, home."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tools.mobile.action_consts import (
    MAX_TEXT_CHARS,
    TAP_IF_PRESENT_MAX_S,
    TAP_TEXT_MAX_QUERY_CHARS,
)
from tools.mobile.fill_label import FILL_MAX_LABEL_CHARS


class Target(BaseModel):
    """Which element an action acts on. At least one selector is required.

    ``id`` IS NOT STABLE, and that is not a detail. ``perception`` numbers
    elements positionally on every dump, and the executor re-dumps after every
    screen-changing action -- so an ``id`` planned from screen N means something
    else on screen N+1. Both blocked runs of 2026-09-04 died on exactly that:
    ``[clear e14, type e14, wait, tap e17]`` reached ``missing_element``,
    because typing into the composer changed the element list.

    ``role`` and ``label`` are computed by this server from the element's own
    content, so they survive a renumber. For any action that follows another
    action, they are the right selector.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default="", max_length=16)
    text: str = Field(default="", max_length=200)
    rid: str = Field(default="", max_length=200)
    role: str = Field(default="", max_length=32)
    label: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def at_least_one_selector(self) -> "Target":
        if not (
            self.id.strip()
            or self.text.strip()
            or self.rid.strip()
            or self.role.strip()
            or self.label.strip()
        ):
            raise ValueError("a target needs one of id, rid, role, label or text")
        return self


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TapAction(_Base):
    op: Literal["tap"]
    target: Target


class TapTextAction(_Base):
    """Tap the one control carrying this visible text.

    Deliberately narrower than ``{"op": "tap", "target": {"text": ...}}``, which
    resolves an ambiguous word to the clickable wrapper and proceeds. This op
    refuses instead -- see :data:`TAP_TEXT_MAX_CANDIDATES`.
    """

    op: Literal["tap_text"]
    text: str = Field(min_length=1, max_length=TAP_TEXT_MAX_QUERY_CHARS)


class TapTextIfPresentAction(_Base):
    """Tap the control carrying this text IF it shows up within ``max_s``.

    For an optional element (a consent prompt, a promo). It polls the screen
    for the text and, when it appears, taps it exactly like ``tap_text``; when
    it never does the step records ``absent`` and the script goes on. It never
    fails the script.
    """

    op: Literal["tap_text_if_present"]
    text: str = Field(min_length=1, max_length=TAP_TEXT_MAX_QUERY_CHARS)
    max_s: float = Field(default=2, gt=0, le=TAP_IF_PRESENT_MAX_S)


class TypeAction(_Base):
    """Type into a field.

    A SECRET value is never carried here. ``secret=true`` requires ``field`` and
    forbids ``text``: the literal lives only in the tester chat turn that
    supplied it, and the executor looks it up by field name at replay time. A
    script that puts a credential in ``text`` is REFUSED BY NAME, because a
    model that can write one into a plan can also write it into a report.
    """

    op: Literal["type"]
    target: Target
    text: str = Field(default="", max_length=MAX_TEXT_CHARS)
    secret: bool = False
    field: str = Field(default="", max_length=80)

    # The literal is refused FIRST, and unconditionally. Checking for a
    # missing `field` first meant a script carrying a credential AND no
    # field got the weaker "needs field" message, burying the finding
    # that matters -- that a secret was written into a plan at all.
    @model_validator(mode="after")
    def secret_comes_from_the_tester(self) -> "TypeAction":
        if self.secret:
            if self.text:
                raise ValueError(
                    "a secret value must never appear in a script; send "
                    '{"op": "type", "target": ..., "field": "<name>", '
                    '"secret": true} and supply the value as tester_input/'
                    "tester_inputs on the same submit"
                )
            if not self.field.strip():
                raise ValueError(
                    "a secret type action needs field (the name the tester was "
                    "asked for) and must leave text empty"
                )
        elif not self.text.strip():
            raise ValueError("type needs text")
        return self


class FillAction(_Base):
    """Type into the field a visible LABEL names -- no selector needed.

    ``label`` is matched against the screen's input fields (see
    ``tools.mobile.fill_label``); more than one field is a refusal, never a
    guess. The value rules are ``type``'s: a SECRET is never carried here, so
    ``secret=true`` requires ``field`` and forbids ``text``.
    """

    op: Literal["fill"]
    label: str = Field(min_length=1, max_length=FILL_MAX_LABEL_CHARS)
    text: str = Field(default="", max_length=MAX_TEXT_CHARS)
    secret: bool = False
    field: str = Field(default="", max_length=80)

    @model_validator(mode="after")
    def value_rules_match_type(self) -> "FillAction":
        if not self.label.strip():
            raise ValueError("fill needs a non-blank label")
        if self.secret:
            if self.text:
                raise ValueError(
                    "a secret value must never appear in a script; send "
                    '{"op": "fill", "label": ..., "field": "<name>", '
                    '"secret": true} and supply the value as tester_input/'
                    "tester_inputs on the same submit"
                )
            if not self.field.strip():
                raise ValueError(
                    "a secret fill action needs field (the name the tester was "
                    "asked for) and must leave text empty"
                )
        elif not self.text.strip():
            raise ValueError("fill needs text")
        return self


class ClearAction(_Base):
    op: Literal["clear"]
    target: Target


class BackAction(_Base):
    op: Literal["back"]


class HomeAction(_Base):
    op: Literal["home"]
