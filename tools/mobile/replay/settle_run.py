"""Replay settle: the one settle path that re-reads the screen after an action."""

from __future__ import annotations

import logging
from typing import Any

from tools.mobile.replay.context import ReplayStop, _append, _result
from tools.mobile.replay.context_consts import (
    NO_AUTO_SETTLE_OPS,
    STATUS_ERROR,
    STATUS_NEEDS_MODEL,
)
from tools.mobile.replay.dump import _dump
from tools.mobile.replay.screen import _stamp_after
from tools.mobile.replay.settle_auto import _auto_settle
from tools.mobile.replay.settle_cut import Cut, CutView, _cut_detail, _cut_of
from tools.mobile.replay.settle_dialog import _answer_dialogs
from tools.mobile.replay.settle_mark import (
    ScreenMark,
    SettleJob,
    SettleOpts,
    _unchanged,
)

logger = logging.getLogger("tools.mobile.executor")


# Re-read the screen after an action that could have changed it.
#
# THE ONE settle path, and it exists because there used to be none for
# ``wait``. ``wait`` is in ``actions.MUTATING_OPS``, but its branch returned
# before the re-dump that membership implied -- so a plain ``wait ms`` NEVER
# re-read the screen, and every assert after a wait was evaluated against the
# PRE-wait screen. An app's reply arrives during exactly that wait. It is why
# ``screen_changed`` was the only kind that appeared to work on the
# 2026-09-04 live run: it compares ids rather than content.
#
# Returns ``(screen, stop)``. ``stop`` is a complete replay result when the
# replay must end here (and this function has already appended the entry),
# and ``None`` when the caller carries on and appends it itself.
#
# *mark_no_change* is False for ``wait``: ``no_change`` exists to tell a model
# "your tap did nothing", and a wait that changes nothing is the normal case.
async def _settle(job: SettleJob, screen: Any, opts: SettleOpts) -> tuple:
    """Re-read the screen after an action that could have changed it."""
    if opts.redump:
        screen, stop = await _settle_redump(job, screen)
        if stop is not None:
            return screen, stop
    # A registered dialog handler answers a known interruption BEFORE the screen
    # is stamped, so the entry records the screen the replay actually continues
    # on. No handler registered: this returns the screen untouched.
    view = CutView(screen, opts.op, opts.extra_expected)
    screen = await _answer_dialogs(job, view)
    # BOTH halves (hash and id), through the one producer, never re-derived.
    _stamp_after(job.entry, screen)
    screen, unchanged = await _settle_unchanged(job, screen, opts)
    if unchanged:
        job.entry["outcome"] = "no_change"
    cut = _cut_of(job.ctx, view._replace(screen=screen))
    if cut.kind:
        return screen, _cut_stop(job, screen, cut)
    return screen, None


async def _settle_redump(job: SettleJob, screen: Any) -> tuple:
    """``_settle``'s re-dump half: ``(screen, stop)``, ``stop`` set on a failed dump."""
    dumped = await _dump(job.ctx)
    if dumped.get("error"):
        job.entry["outcome"] = "dump_failed"
        job.entry["detail"] = str(dumped["error"])[:400]
        _append(job.trace, job.entry)
        return screen, _result(
            ReplayStop(
                STATUS_ERROR, job.trace, screen, "", job.entry["detail"], job.step_no
            )
        )
    return dumped.get("content"), None


def _mark_of(entry: dict, which: str) -> ScreenMark:
    """The ScreenMark of the entry's ``before`` or ``after`` read."""
    if which == "before":
        return ScreenMark(
            str(entry.get("before_screen_hash") or ""),
            str(entry.get("before_screen_id") or ""),
        )
    return ScreenMark(entry["after_screen_hash"], entry["after_screen_id"])


# ``(screen, unchanged)``: did the action change nothing, after auto-settle.
async def _settle_unchanged(job: SettleJob, screen: Any, opts: SettleOpts) -> tuple:
    """``(screen, unchanged)``: did the action change nothing, after auto-settle."""
    before = _mark_of(job.entry, "before")
    unchanged = opts.mark_no_change and _unchanged(before, _mark_of(job.entry, "after"))
    # AUTO-SETTLE only on a read this call took itself: a skipped redump
    # (`launch` brought to front) already decided nothing moved, and a wait's
    # own poll (`mark_no_change=False`) is its own settle.
    # A script whose next op waits for the screen to change observes it anyway.
    skip = str(opts.op or "") in NO_AUTO_SETTLE_OPS or opts.before_change_wait
    if unchanged and opts.redump and before.content_hash and not skip:
        read = await _auto_settle(job.ctx, screen, before.content_hash)
        _stamp_after(job.entry, read)
        unchanged = _unchanged(before, _mark_of(job.entry, "after"))
        # PROVEN only when the settle took a fresh read (a failed dump hands
        # the old screen back): the learner never calls a tap dead otherwise.
        job.entry["settled_unchanged"] = unchanged and read is not screen
        screen = read
    return screen, unchanged


# The replay result for an app that is not where the script thinks it is.
#
# Recorded on the ACTION that took it
# there, and the replay stops: every target after this one would miss against
# a screen that belongs to somebody else, which is how one mistaken tap spent
# a case's three escapes and reported `blocked` without ever mentioning that
# the app had been left.
def _cut_stop(job: SettleJob, screen: Any, cut: Cut) -> dict:
    """The replay result for an app that is not where the script thinks it is."""
    entry, kind = job.entry, cut.kind
    entry["outcome"] = "system_dialog" if kind == "system_dialog" else "left_app"
    entry["detail"] = _cut_detail(cut, job.ctx, entry)
    _stamp_after(entry, screen)
    _append(job.trace, entry)
    return _result(
        ReplayStop(
            STATUS_NEEDS_MODEL, job.trace, screen, "", entry["detail"], job.step_no
        )
    )
