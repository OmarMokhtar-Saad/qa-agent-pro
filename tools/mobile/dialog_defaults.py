"""Shared parts of the default dialog handlers (package installer, permissions).

A :class:`ChoiceHandler` is a choice list: the controls it is willing to tap,
best first, recognised by the foreground package. It decides ONLY from controls
present in the tree, and it stays out of any screen that mentions an uninstall:
the system uninstall dialog confirms with a plain ``OK``, which no denylist term
can catch, so the one safe rule is to never act on that screen at all. It also
stays out of an installer CONFLICT screen (signature mismatch), which the
app_tester_ui foreign-stop reads to report the mismatch.

``dialog_handlers.handle`` still enforces the central rules (expected app
untouched, visible control only, denylist) on whatever a handler returns.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from tools.mobile import dialog_handlers as dh

#: A screen whose raw hierarchy contains any of these (case-insensitive) is never
#: answered by a default handler. Deliberately broad: a missed handler falls back
#: to the executor's normal refusal, while a wrong tap loses a tester's app.
UNINSTALL_MARKERS = ("uninstall", "delete", "clear data", "clear storage")

#: An installer screen that reports a CONFLICT (signature mismatch, "conflicts with
#: an existing package", "App not installed") is never dismissed by a default
#: handler: the app_tester_ui foreign-stop reads exactly that dialog to tell the
#: tester the build is signed differently, and a handler that tapped Done/OK would
#: erase the evidence.
CONFLICT_MARKERS = (
    "conflicts with",
    "existing package",
    "app not installed",
    "not installed as",
    "signature",
    "signatures",
)


def mentions_uninstall(xml: str) -> bool:
    lowered = (xml or "").lower()
    return any(marker in lowered for marker in UNINSTALL_MARKERS)


def mentions_conflict(xml: str) -> bool:
    lowered = (xml or "").lower()
    return any(marker in lowered for marker in CONFLICT_MARKERS)


def leave_alone(xml: str) -> bool:
    """True for a screen no default handler may answer (uninstall or conflict)."""
    return mentions_uninstall(xml) or mentions_conflict(xml)


def _present(action: dh.DialogAction, texts: set, rids: set) -> bool:
    if action.kind == dh.TAP_TEXT:
        return action.value in texts
    if action.kind == dh.TAP_RID:
        return action.value in rids
    return False


@dataclass(frozen=True)
class ChoiceHandler:
    """Tap the first control of ``choices`` that is on screen, in order."""

    name: str
    packages: frozenset
    choices: tuple

    def matches(self, ctx: dh.DialogContext) -> bool:
        return ctx.package in self.packages and not leave_alone(ctx.xml)

    def decide(self, ctx: dh.DialogContext) -> Optional[dh.DialogAction]:
        if not self.matches(ctx):
            return None
        texts = dh.visible_texts(ctx.xml)
        rids = dh.visible_resource_ids(ctx.xml)
        for action in self.choices:
            if _present(action, texts, rids):
                return action
        return None


def register_default_handlers() -> list:
    """Register the package-installer and permission handlers; safe to repeat.

    ``register_handler`` replaces a handler of the same name, so calling this twice
    leaves one copy of each. Returns the registered handler names.
    """
    from tools.mobile import dialog_install, dialog_permission

    for handler in (dialog_install.HANDLER, dialog_permission.HANDLER):
        dh.register_handler(handler)
    return dh.handler_names()
