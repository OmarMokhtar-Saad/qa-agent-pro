"""The permission choices a settle step hands the dialog handlers, and its stop notes."""

from __future__ import annotations

from typing import Any

from tools.mobile import dialog_handlers, perm_groups
from tools.mobile.replay.context import Context


def dialog_context(ctx: Context, foreign: str, seen: tuple) -> Any:
    """The handlers' view of the dialog: screen, plus the run's permission choices."""
    perms = getattr(ctx, "permissions", None) or {}
    return dialog_handlers.DialogContext(
        serial=ctx.serial,
        package=foreign,
        expected_apps=seen[0],
        xml=seen[1],
        deny_permissions=perm_groups.groups_of(perms.get("deny")),
        grant_permissions=perms.get("grant") is not False,
    )


def record_stop(entry: dict, step: dict) -> bool:
    """Note an unclear or refused answer on the trace entry; True when the loop stops."""
    if step.get("unclear"):
        entry["permission_unclear"] = step["unclear"]
    elif step.get("refused"):
        entry["dialog_refused"] = step["refused"]
    else:
        return False
    return True


def mark_unclear(entry: dict) -> None:
    """A pop-up the handler could not map to the deny list: say so, hand it back."""
    text = entry.get("permission_unclear")
    if text:
        entry["outcome"] = "permission_unclear"
        entry["detail"] = "Permission pop-up is unclear (%s). %s" % (
            text,
            entry["detail"],
        )
