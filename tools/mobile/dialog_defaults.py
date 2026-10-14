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

from typing import cast

from tools.mobile import dialog_handlers as dh
from tools.mobile import dialog_install, dialog_permission
from tools.mobile.dialog_choice import (  # noqa: F401
    CONFLICT_MARKERS,
    UNINSTALL_MARKERS,
    ChoiceHandler,
    _present,
    leave_alone,
    mentions_conflict,
    mentions_uninstall,
)


def register_default_handlers() -> list:
    """Register the package-installer and permission handlers; safe to repeat.

    ``register_handler`` replaces a handler of the same name, so calling this twice
    leaves one copy of each. Returns the registered handler names.
    """
    for handler in (dialog_install.HANDLER, dialog_permission.HANDLER):
        dh.register_handler(cast("dh.DialogHandler", handler))
    return dh.handler_names()
