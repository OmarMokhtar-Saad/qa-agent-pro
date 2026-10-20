"""Which packages are never the app under test (installer, App Tester, launcher).

A run pinned to one of these keeps cutting turns as "left the app" (B05), files
its evidence under the wrong app (B06) and finds no learned flows (B13). Knows
no app: the sets are the platform's own surfaces.
"""

from __future__ import annotations

from tools.mobile.render_install import APP_TESTER_PACKAGES
from tools.mobile.replay.screen_pkg import (
    LAUNCHER_PACKAGES,
    PLATFORM_PACKAGES,
    SYSTEM_DIALOG_PACKAGES,
)

TRANSIENT: frozenset[str] = (
    SYSTEM_DIALOG_PACKAGES
    | frozenset(APP_TESTER_PACKAGES)
    | LAUNCHER_PACKAGES
    | PLATFORM_PACKAGES
)


def transient(package: object) -> bool:
    """True for an installer, permission, App Tester, launcher or platform package."""
    return str(package or "").strip() in TRANSIENT


def real_app(package: object) -> bool:
    """True for a non-empty package that can be an app under test."""
    text = str(package or "").strip()
    return bool(text) and text not in TRANSIENT


def repin(ctx: object, foreground: str) -> bool:
    """Move a transient pin of *ctx* to the real foreground app; True when moved."""
    if transient(getattr(ctx, "package", "")) and real_app(foreground):
        ctx.package = foreground  # type: ignore[attr-defined]
        return True
    return False
