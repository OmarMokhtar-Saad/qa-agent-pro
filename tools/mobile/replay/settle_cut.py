"""Replay settle: why a replay must stop on a screen (system dialog, left the app)."""

from __future__ import annotations

from typing import Any, NamedTuple

from tools.mobile.replay.context import Context
from tools.mobile.replay.screen import (
    LEAVES_ON_PURPOSE,
    _screen_package,
    left_app_detail,
    system_dialog_detail,
    system_dialog_package,
)
from tools.mobile.replay.settle_mark import ScreenMark, _unchanged


class CutView(NamedTuple):
    """The screen a step ended on, the op that got there, packages expected in front."""

    screen: Any
    op: str
    extra: Any = ()


class Cut(NamedTuple):
    """Why the replay must stop: the outcome kind and the foreign package."""

    kind: str
    foreign: str


# Packages the step expects in front besides the run's own app: the
# context's ``expect_apps`` plus *extra* (the package a ``launch`` just
# opened). Empty values are dropped, so a missing one widens nothing.
def _expected_here(ctx: Context, extra: Any = ()) -> frozenset:
    """Packages the step expects in front besides the run's own app."""
    declared = (*tuple(getattr(ctx, "expect_apps", ()) or ()), *tuple(extra or ()))
    return frozenset(str(p) for p in declared if p)


# ``(outcome, package)`` for why the replay must stop on the screen, or
# ``('', '')``. THE one place the system-dialog and left-the-app rules are
# decided, so the cut and the dialog answerer cannot disagree. A package in
# ``expect_apps`` (or *extra*) is not another app.
def _cut_of(ctx: Context, view: CutView) -> Cut:
    """``Cut(outcome, package)`` for why the replay must stop on the screen, or empty."""
    dialog = system_dialog_package(view.screen)
    if dialog:
        return Cut("system_dialog", dialog)
    package = _screen_package(view.screen)
    if (
        package
        and ctx.package
        and package != ctx.package
        and package not in _expected_here(ctx, view.extra)
        and str(view.op or "") not in LEAVES_ON_PURPOSE
    ):
        return Cut("left_app", package)
    return Cut("", "")


# The refusal sentence for a cut, plus the guard's own sentence when a
# handler's tap was refused (so the model learns WHY nothing was answered).
def _cut_detail(cut: Cut, ctx: Context, entry: dict) -> str:
    """The refusal sentence for a cut, plus the guard's own sentence when refused."""
    base = (
        system_dialog_detail(cut.foreign)
        if cut.kind == "system_dialog"
        else left_app_detail(cut.foreign, ctx.package)
    )
    refused = str(entry.get("dialog_refused") or "")
    return (base + " " + refused).strip() if refused else base


# A cut is about where an action TOOK the device. A screen identical to the one
# the action started from was not reached by it: the app may still be on its way
# (a tap on "Open" in an installer launches the app under test a moment later)
# or the read is stale (a dialog the model just answered). Cutting on it stops a
# turn mid-way on a screen the model already saw, so only a MOVED screen cuts.
def moved_by_action(entry: dict) -> bool:
    """Whether the entry's after-read differs from its before-read (unknown: yes)."""
    before = ScreenMark(
        str(entry.get("before_screen_hash") or ""),
        str(entry.get("before_screen_id") or ""),
    )
    if not (before.content_hash or before.screen_id):
        return True
    after = ScreenMark(
        str(entry.get("after_screen_hash") or ""),
        str(entry.get("after_screen_id") or ""),
    )
    return not _unchanged(before, after)
