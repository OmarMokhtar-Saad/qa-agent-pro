"""Runtime permission dialog: allow by default, decline only what the test denies.

A step that needs a permission used to be cut at the system dialog. The handler
taps the narrowest grant on offer ("While using the app", then exactly "Allow").
It never taps "Allow all the time" (widening a grant is the tester's call; the
central denylist refuses it as well). Matching is on the EXACT visible text.

When the run carries a deny list, the pop-up question is read and mapped to
permission groups (``perm_keywords``): every group denied -> tap the decline
button; every group allowed -> allow; unclear or mixed -> stop with
``permission_unclear`` and hand the pop-up back to the host. With
``grant_permissions=false`` nothing is tapped and the pop-up is cut for the host.

The legacy ``com.android.packageinstaller`` package also hosted permission
dialogs before Android 10, so it is listed here as well; on an installer screen
(no decline button) the allow choices decide, and the next handler is tried.
"""

from __future__ import annotations

import html
import re
from typing import Optional

from tools.mobile import dialog_handlers as dh
from tools.mobile import perm_groups, perm_keywords
from tools.mobile.dialog_choice import ChoiceHandler

PERMISSION_PACKAGES = frozenset(
    {
        "com.google.android.permissioncontroller",
        "com.android.permissioncontroller",
        "com.android.packageinstaller",
    }
)

#: Best first.
PERMISSION_BUTTONS = ("While using the app", "Allow")

_ALLOW = ChoiceHandler(
    name="permission_controller",
    packages=PERMISSION_PACKAGES,
    choices=tuple(
        dh.DialogAction(dh.TAP_TEXT, text, "permission: " + text)
        for text in PERMISSION_BUTTONS
    ),
)

# "Don't allow" (curly apostrophe folded), "Deny", Arabic "do not allow" / "refuse".
_DECLINE = (
    "don't allow",
    "deny",
    "\u0639\u062f\u0645 \u0627\u0644\u0633\u0645\u0627\u062d",
    "\u0631\u0641\u0636",
)
_MESSAGE = re.compile(
    r'<node[^>]*?text="([^"]*)"[^>]*?resource-id="[^"]*permission_message[^"]*"'
    r'|<node[^>]*?resource-id="[^"]*permission_message[^"]*"[^>]*?text="([^"]*)"'
)


def _fold(text: str) -> str:
    return text.replace("’", "'").strip().lower()


def _decline_button(texts: set) -> str:
    """The visible decline label ("" when none); never an "ask again" variant."""
    for text in sorted(texts):
        if _fold(text) in _DECLINE:
            return text
    return ""


def _message(xml: str, texts: set) -> str:
    """The pop-up question: the permission_message node, else every non-button text."""
    found = _MESSAGE.search(xml)
    if found:
        return html.unescape(found.group(1) or found.group(2) or "")
    buttons = {_fold(t) for t in (*PERMISSION_BUTTONS, *_DECLINE)}
    return " ".join(sorted(t for t in texts if _fold(t) not in buttons))


# "Allow <App name> to access your location?": the app's own name ("Photos",
# "Camera Pro") must not count as a permission keyword. Other wordings (other
# locales) are left whole.
_APP_NAME = re.compile(
    r"^\s*allow\s+.+?\s+(?=to\s+(?:access|take|record|send|read|make|see|find|use|"
    r"connect|receive|write|post|know|discover|get|manage|scan|advertise)\b)",
    re.IGNORECASE,
)


def _without_app(text: str) -> str:
    """*text* with the leading "Allow <app name>" of the English question removed."""
    return _APP_NAME.sub("", text, count=1)


def _ungrouped(ctx: dh.DialogContext, hit: frozenset, groups: frozenset) -> bool:
    """A denied raw permission no group covers cannot be matched to this pop-up."""
    loose = {name for name in ctx.deny_permissions if name not in perm_groups.GROUPS}
    return bool(loose) and not (hit and hit == groups)


def _unclear(text: str) -> dh.DialogAction:
    return dh.DialogAction(dh.STOP, text[:300], "permission_unclear")


class PermissionHandler:
    """Allow every permission pop-up; decline only the groups the test denies."""

    name = "permission_controller"
    choices = _ALLOW.choices

    def matches(self, ctx: dh.DialogContext) -> bool:
        return _ALLOW.matches(ctx)

    def decide(self, ctx: dh.DialogContext) -> Optional[dh.DialogAction]:
        if not ctx.grant_permissions or not self.matches(ctx):
            return None
        if not ctx.deny_permissions:
            return _ALLOW.decide(ctx)
        texts = dh.visible_texts(ctx.xml)
        button = _decline_button(texts)
        text = _message(ctx.xml, texts)
        if not button:
            # A denied permission must never be allowed for want of a button.
            return _unclear(text) if ctx.package in CONTROLLERS else _ALLOW.decide(ctx)
        groups = perm_keywords.groups_in(_without_app(text))
        hit = groups & ctx.deny_permissions
        if not groups or (hit and hit != groups) or _ungrouped(ctx, hit, groups):
            return _unclear(text)
        if hit:
            return dh.DialogAction(dh.TAP_TEXT, button, "permission: " + button)
        return _ALLOW.decide(ctx)


HANDLER = PermissionHandler()


def is_permission_button(text: object) -> bool:
    """Whether *text* is the label of a runtime-permission dialog button."""
    return str(text or "").strip().lower() in {b.lower() for b in PERMISSION_BUTTONS}


#: The packages of the permission-controller dialog (not the legacy installer).
CONTROLLERS = frozenset(p for p in PERMISSION_PACKAGES if "permissioncontroller" in p)

GRANT_HINT = (
    " The front window is a system permission prompt. Tapping Allow or "
    "'Only this time' CHANGES the device's permissions. If the prompt names "
    "another app (not the app under test), do not answer it unless the brief "
    "allows permission changes: ask the user, or use Deny or `back`. For the "
    "app under test, rerun with `grant_permissions=true` (the default) to "
    "grant up front (test setup, not a settings change); pass `false` only when the test is the "
    "permission prompt itself."
)


def front_hint(screen: object) -> str:
    """``GRANT_HINT`` when *screen*'s front window is the permission controller."""
    body = screen if isinstance(screen, dict) else {}
    front = {str(body.get("package") or ""), str(body.get("dialog_package") or "")}
    return GRANT_HINT if front & CONTROLLERS else ""
