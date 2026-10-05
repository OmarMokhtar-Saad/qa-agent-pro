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

import html
import re
from dataclasses import dataclass
from typing import Optional, Protocol

TAP_TEXT = "tap_text"
TAP_RID = "tap_rid"
BACK = "back"

_TEXT_ATTR = re.compile(r'(?:^|\s)text="([^"]*)"')
_RID_ATTR = re.compile(r'resource-id="([^"]*)"')


@dataclass(frozen=True)
class DialogContext:
    serial: str
    package: str
    expected_apps: frozenset = frozenset()
    xml: str = ""


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
    return {html.unescape(m) for m in _TEXT_ATTR.findall(xml or "") if m}


def visible_resource_ids(xml: str) -> set:
    return {html.unescape(m) for m in _RID_ATTR.findall(xml or "") if m}


#: Controls no handler may ever tap, matched case-insensitively against the
#: action's text or resource-id. Losing data or widening a grant is the tester's
#: call, never a dialog handler's.
_DENY_TERMS = (
    "uninstall",
    "delete",
    "clear data",
    "clear storage",
    "clear_data",
    "clear_storage",
    "allow all the time",
    "allow_all_the_time",
    "always_allow",
    "allow_always",
    "allow_all",
    "allow always",
    "always allow",
    "allow all",
    "erase",
    "factory reset",
    "factory_reset",
    "force stop",
    "force_stop",
    "remove",
    "disable",
)


def is_denied(action: DialogAction) -> bool:
    """True when the action would tap a control on the denylist."""
    if action.kind == BACK:
        return False
    value = " ".join(action.value.lower().split())
    return any(term in value for term in _DENY_TERMS)


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
        if action is None or is_denied(action):
            continue
        if _control_is_visible(action, ctx):
            return action
    return None
