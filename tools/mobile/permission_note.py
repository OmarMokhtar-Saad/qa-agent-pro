"""The step-reply note for a runtime-permission pop-up, whichever package shows it."""

from __future__ import annotations

import re

from tools.mobile import dialog_permission

#: The controller's own buttons: ``permission_allow*_button`` / ``permission_deny*_button``.
_BUTTON_RID = re.compile(r"[/:]permission_(allow|deny)\w*_button$")

NOTE = (
    "A runtime-permission dialog is open in front of the app{asker}. "
    "Choose Allow or Deny explicitly with the dialog's own button; do not close "
    "it with Back, which records a denial on the device."
)


def _is_button(element: object) -> bool:
    rid = str(element.get("rid") or "") if isinstance(element, dict) else ""
    return bool(_BUTTON_RID.search(rid))


_PACKAGE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+")
GENERIC_ASKER = " (an app's permission dialog)"


def _asker(body: dict) -> str:
    """The asking package, else a generic name. Never the dialog's own text."""
    for key in ("dialog_package", "package"):
        app = str(body.get(key) or "")
        if app not in dialog_permission.CONTROLLERS and _PACKAGE.fullmatch(app):
            return " (" + app[:80] + " is asking)"
    return GENERIC_ASKER


def choice_note(screen: object) -> str:
    """``NOTE`` when *screen* carries a permission pop-up from ANY package, else ""."""
    body = screen if isinstance(screen, dict) else {}
    if not any(_is_button(e) for e in body.get("elements") or []):
        return ""
    return NOTE.format(asker=_asker(body))


def annotate(reply: object, screen: object) -> None:
    """Put ``permission_note`` on the *reply* dict (never on a stored document)."""
    note = choice_note(screen)
    if note and isinstance(reply, dict):
        reply["permission_note"] = note
