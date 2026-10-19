"""Dialog handlers: answer the standard system dialogs a step walks into.

A run that installs or updates an app crosses the package installer, the
permission controller and the App Tester itself. Those screens belong to another
app, so the executor used to cut the script ("screen now belongs to another app").
A handler turns a known dialog into ONE tap, under three rules enforced HERE, not
in each handler:

* the foreground app the step expected (``expected_apps``) is never an
  interruption, so no handler runs for it;
* the action must name a control that is present in the current tree -- no blind
  taps; an action naming an absent control is dropped;
* a control on the denylist (uninstall, delete, clear data, "allow all the
  time", ...) is never tapped, whatever a handler decides;
* a handler decides only for dialogs it recognises; unknown dialogs return None
  and the executor falls back to its normal refusal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

from tools.mobile.deny_terms import (
    has_deny_term,
    is_control_label,
    leads_with_deny_term,
)
from tools.mobile.xml_attrs import attr_pattern, attr_values

TAP_TEXT = "tap_text"
TAP_RID = "tap_rid"
BACK = "back"
# No tap: the handler cannot tell what the dialog asks, so the script stops and the
# host answers it. ``value`` is the reason (the dialog's own text).
STOP = "stop"

_TEXT_ATTR = attr_pattern("text")
_RID_ATTR = attr_pattern("resource-id")


@dataclass(frozen=True)
class DialogContext:
    serial: str
    package: str
    expected_apps: frozenset = frozenset()
    xml: str = ""
    # The permission groups the test prompt denies, and whether pop-ups may be answered.
    deny_permissions: frozenset = frozenset()
    grant_permissions: bool = True


@dataclass(frozen=True)
class DialogAction:
    kind: str
    value: str = ""
    reason: str = ""


class DialogHandler(Protocol):
    name: str

    def matches(self, ctx: DialogContext) -> bool: ...

    def decide(self, ctx: DialogContext) -> Optional[DialogAction]: ...


_HANDLERS: list = []


def register_handler(handler: DialogHandler) -> None:
    """Add a handler; a second registration under the same name replaces it."""
    unregister_handler(handler.name)
    _HANDLERS.append(handler)


def unregister_handler(name: str) -> None:
    _HANDLERS[:] = [h for h in _HANDLERS if h.name != name]


def handler_names() -> list:
    return [h.name for h in _HANDLERS]


def visible_texts(xml: str) -> set:
    return {value for value in attr_values(_TEXT_ATTR, xml) if value}


def visible_resource_ids(xml: str) -> set:
    return {value for value in attr_values(_RID_ATTR, xml) if value}


def is_denied(action: DialogAction) -> bool:
    """True when the action would tap a control on the denylist.

    The terms live in ``deny_terms`` and match as whole words; visible text
    longer than a control label (a paragraph) is judged by its opening words
    only, and a resource id always is judged, whatever its length.
    """
    if action.kind == BACK:
        return False
    if action.kind == TAP_TEXT and not is_control_label(action.value):
        return leads_with_deny_term(action.value)
    return has_deny_term(action.value)


def _control_is_visible(action: DialogAction, ctx: DialogContext) -> bool:
    if action.kind == BACK:
        return True
    if action.kind == TAP_TEXT:
        return action.value in visible_texts(ctx.xml)
    if action.kind == TAP_RID:
        return action.value in visible_resource_ids(ctx.xml)
    return False


def handle(ctx: DialogContext) -> Optional[DialogAction]:
    """The action for the first handler that recognises the dialog, or None."""
    if ctx.package in ctx.expected_apps:
        return None
    for handler in list(_HANDLERS):
        if not handler.matches(ctx):
            continue
        action = handler.decide(ctx)
        if action is not None and action.kind == STOP:
            return action
        if action is None or is_denied(action):
            continue
        if _control_is_visible(action, ctx):
            return action
    return None
