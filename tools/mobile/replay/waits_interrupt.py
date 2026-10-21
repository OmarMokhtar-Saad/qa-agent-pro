"""Replay waits: what a watched wait counts as an interruption."""

from __future__ import annotations

from tools.mobile import target_pin, watch
from tools.mobile.replay.context import Context
from tools.mobile.replay.screen import (
    _screen_package,
    _texts,
    left_app_detail,
    system_dialog_detail,
    system_dialog_package,
)


def _left_the_app(ctx: Context, package: str) -> bool:
    """Whether *package* is in front and is not the run's app or one it expects.

    An installer's "Open" brings the app under test forward: a transient pin
    (installer, App Tester) follows that app, and ``expect_apps`` stay in scope.
    """
    if not (package and ctx.package) or package == ctx.package:
        return False
    if package in tuple(getattr(ctx, "expect_apps", ()) or ()):
        return False
    return not target_pin.repin(ctx, package)


def _interruption_of(ctx: Context, screen: object, baseline: object) -> str:
    """``"interrupted: <what>"`` for what is NEW on *screen* since *baseline*.

    ``""`` when nothing new took the screen. Only NEW facts count: a dialog
    that was already up when the wait began is the screen the model chose to
    wait on, not an interruption of it.
    """
    package = _screen_package(screen)
    before_package = _screen_package(baseline)
    named = watch.classify_package(package, before_package)
    if named:
        return named
    dialog = system_dialog_package(screen)
    if dialog and dialog != system_dialog_package(baseline):
        return watch.PREFIX + system_dialog_detail(dialog)
    if _left_the_app(ctx, package) and before_package != package:
        return watch.PREFIX + left_app_detail(package, ctx.package)
    return watch.classify_texts(_texts(screen) - _texts(baseline))
