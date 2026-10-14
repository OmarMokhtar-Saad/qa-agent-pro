"""The navigation action models: press, scroll, wait, launch, open_url."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field, model_validator

from tools.mobile.action_consts import (
    MAX_WAIT_MS,
    URL_SCHEMES,
    WAIT_UNTIL_DEFAULT_S,
    WAIT_UNTIL_MAX_S,
    is_package_id,
)
from tools.mobile.action_models_basic import Target, _Base
from tools.mobile.adb import SAFE_URL_RE

# ``PressAction``'s docstring ships to the model inside ``response_schema()``
# on every turn, so it carries the CONTRACT only. The reasoning lives here.
#
# ``target`` is the FIELD, and it is required so the key lands where the model
# meant: the executor taps it to focus it first, the same two steps ``type``
# and ``clear`` take, and that focus tap is judged by the destructive guard
# exactly like any tap -- named element AND the control under the finger.
#
# What the key then does is the APP's decision, not the field's. ``enter``
# delivers the form's IME action (send / go / done), and no dump names the
# control that action fires -- uiautomator emits no ``imeOptions``. So the
# guard judges a press against every element the packet carries (and refuses
# on a packet that does not carry the whole screen): on a screen holding an
# irreversible control the press is handed to the tester, and the model is told
# to tap the control it means instead, which IS guarded by name. A press was
# shipped once with "requiring the field is what makes the guard see it";
# measured, `press enter {"rid": amount}` sent KEYCODE_ENTER on a form whose
# button `tap` refused. The field's label was never the node the key actuated.


class PressAction(_Base):
    """Press a keyboard key against the named FIELD: tap it to focus, then
    send ``key``. ``enter`` is the keyboard's action key; what it submits is
    the app's decision, so a press is judged against every control on the
    screen and handed to the tester when any looks irreversible."""

    op: Literal["press"]
    key: Literal["enter"]
    target: Target


class ScrollAction(_Base):
    """``dir`` is the way the content moves: "down" reveals what is below."""

    op: Literal["scroll"]
    dir: Literal["up", "down", "left", "right"]
    target: Optional[Target] = None


class WaitAction(_Base):
    op: Literal["wait"]
    ms: int = Field(default=0, ge=0, le=MAX_WAIT_MS)
    until_text: str = Field(default="", max_length=200)
    # Wait for an ELEMENT by its resource id (present, or gone with
    # ``until_gone``), optionally one whose text/desc contains ``until_rid_text``.
    until_rid: str = Field(default="", max_length=200)
    until_gone: bool = False
    until_rid_text: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def something_to_wait_for(self) -> "WaitAction":
        has_rid = bool(self.until_rid.strip())
        if (self.until_gone or self.until_rid_text.strip()) and not has_rid:
            raise ValueError("until_gone and until_rid_text need until_rid")
        if has_rid and self.until_text.strip():
            raise ValueError("wait takes until_text OR until_rid, not both")
        if not self.ms and not self.until_text.strip() and not has_rid:
            raise ValueError("wait needs ms, until_text or until_rid")
        return self


class WaitUntilAction(_Base):
    """A condition wait. ``text`` is what ``wait_until_text`` waits to see and
    what ``wait_until_gone`` waits to lose; the other two take none."""

    op: Literal[
        "wait_until_changed", "wait_until_text", "wait_until_gone", "wait_until_idle"
    ]
    max_s: float = Field(default=WAIT_UNTIL_DEFAULT_S, gt=0, le=WAIT_UNTIL_MAX_S)
    text: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def text_where_it_is_the_condition(self) -> "WaitUntilAction":
        needs_text = self.op in ("wait_until_text", "wait_until_gone")
        if needs_text and not self.text.strip():
            raise ValueError(self.op + " needs the text it waits for")
        if not needs_text and self.text.strip():
            raise ValueError(self.op + " takes no text")
        return self


class LaunchAction(_Base):
    # No docstring on purpose: it would ship to the model in every response_schema.
    # ``package`` None reopens the run's own app; a package opens ONE other app, and
    # launch_guard.launch_allowed decides which at replay time.
    op: Literal["launch"]
    package: Optional[str] = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def package_is_well_formed(self) -> "LaunchAction":
        if self.package is not None and not is_package_id(self.package):
            raise ValueError(
                "launch package must be an Android package id such as com.example.app"
            )
        return self


class ClearAppDataAction(_Base):
    """Wipe the run's OWN app's data (``pm clear``) and relaunch it.

    No ``target`` and no package field: this is a whole-app op, not a tap on
    an element, and the executor resolves the package from ``ctx.package``
    only -- a script cannot name a DIFFERENT app to wipe. Guarded like every
    other destructive action even though it names no on-screen node; see
    ``action_text``'s special case and ``executor.NON_ACTUATING_OPS``.
    """

    op: Literal["clear_app_data"]


class OpenUrlAction(_Base):
    op: Literal["open_url"]
    url: str = Field(max_length=2000)

    @model_validator(mode="after")
    def allowed_scheme(self) -> "OpenUrlAction":
        scheme = self.url.split(":", 1)[0].strip().lower()
        if scheme not in URL_SCHEMES:
            raise ValueError("url scheme must be one of " + ", ".join(URL_SCHEMES))

        if not SAFE_URL_RE.fullmatch(self.url):
            raise ValueError("url contains whitespace or unsafe characters")
        return self
