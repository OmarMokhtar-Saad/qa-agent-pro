"""Replay settle: the one settle path that re-reads the screen after an action."""

from __future__ import annotations

import logging
from typing import Any

from tools.mobile import actions as actions_mod
from tools.mobile.replay.context import ReplayStop, _append, _result
from tools.mobile.replay.context_consts import (
    NO_AUTO_SETTLE_OPS,
    STATUS_ERROR,
    STATUS_NEEDS_MODEL,
)
from tools.mobile.replay.dump import _dump
from tools.mobile.replay.screen import _stamp_after
from tools.mobile.replay.settle_auto import (
    Settled,
    _auto_settle,  # noqa: F401  (public-names shim)
    _auto_settle_seen,
    _probe_skip,
)
from tools.mobile.replay.settle_cut import (
    Cut,
    CutView,
    _cut_detail,
    _cut_of,
    moved_by_action,
)
from tools.mobile.replay.settle_dialog import _answer_dialogs
from tools.mobile.replay.settle_mark import (
    ScreenMark,
    SettleJob,
    SettleOpts,
    _unchanged,
)

logger = logging.getLogger("tools.mobile.executor")

NO_CHANGE = "no_change"
UNVERIFIED = "unverified"
UNVERIFIED_DETAIL = (
    "The screen's picture changed after this action but its element tree did "
    "not; the change could not be confirmed -- look at the screenshot."
)


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
        screen, stop = await _settle_redump(job, screen, opts)
        if stop is not None:
            return screen, stop
    # A registered dialog handler answers a known interruption BEFORE the screen
    # is stamped, so the entry records the screen the replay actually continues
    # on. No handler registered: this returns the screen untouched.
    view = CutView(screen, opts.op, opts.extra_expected)
    screen = await _answer_dialogs(job, view)
    # BOTH halves (hash and id), through the one producer, never re-derived.
    _stamp_after(job.entry, screen)
    screen, outcome = await _settle_unchanged(job, screen, opts)
    _record_outcome(job.entry, outcome)
    cut = _cut_of(job.ctx, view._replace(screen=screen))
    if cut.kind and moved_by_action(job.entry):
        return screen, _cut_stop(job, screen, cut)
    return screen, None


def _no_auto_settle(screen: Any, opts: SettleOpts) -> bool:
    """True when no auto-settle follows this op (the probe skip needs one to)."""
    skip = str(opts.op or "") in NO_AUTO_SETTLE_OPS or opts.before_change_wait
    return skip or _target_in_hand(opts.next_target, screen)


def _skip_eligible(screen: Any, opts: SettleOpts) -> bool:
    """True when the settle would auto-settle after its dump (the same cases)."""
    return opts.mark_no_change and not _no_auto_settle(screen, opts)


async def _settle_redump(job: SettleJob, screen: Any, opts: SettleOpts) -> tuple:
    """``_settle``'s re-dump half: ``(screen, stop)``, ``stop`` set on a failed dump.

    The dump is skipped, and the screen in hand kept, when the frame probe stayed
    equal to that screen's trusted pre-action baseline for the whole settle window.
    """
    if _skip_eligible(screen, opts) and await _probe_skip(job.ctx, screen):
        job.entry["probe_skip"] = True
        return screen, None
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


def _target_in_hand(target: Any, screen: Any) -> bool:
    """True when the next typed field is already on *screen* (nothing to wait for)."""
    if target is None or not isinstance(screen, dict):
        return False
    hit = actions_mod.resolve_target(target, screen) or {}
    return (hit.get("content") or {}).get("element") is not None


# ``(screen, outcome)``: ``"no_change"``, ``"unverified"`` (the frame moved or
# the read failed while the tree stayed the same) or ``""`` (it changed).
async def _settle_unchanged(job: SettleJob, screen: Any, opts: SettleOpts) -> tuple:
    """``(screen, outcome)``: did the action change nothing, after auto-settle."""
    before = _mark_of(job.entry, "before")
    unchanged = opts.mark_no_change and _unchanged(before, _mark_of(job.entry, "after"))
    # AUTO-SETTLE only on a read this call took itself: a skipped redump
    # (`launch` brought to front) already decided nothing moved, and a wait's
    # own poll (`mark_no_change=False`) is its own settle.
    # A script whose next op waits for the screen to change observes it anyway.
    skip = _no_auto_settle(screen, opts) or job.entry.get("probe_skip") is True
    if not (unchanged and opts.redump and before.content_hash and not skip):
        return screen, NO_CHANGE if unchanged else ""
    seen = await _auto_settle_seen(job.ctx, screen, before.content_hash)
    return seen.screen, _settled_outcome(job, screen, seen)


def _record_outcome(entry: dict, outcome: str) -> None:
    """Write the settle's verdict onto the entry; ``""`` leaves it alone."""
    if outcome:
        entry["outcome"] = outcome
    if outcome == UNVERIFIED:
        entry["detail"] = UNVERIFIED_DETAIL


def _settled_outcome(job: SettleJob, screen: Any, seen: Settled) -> str:
    """Stamp the settle's read; ``""``, ``"no_change"`` or ``"unverified"``."""
    _stamp_after(job.entry, seen.screen)
    if not _unchanged(_mark_of(job.entry, "before"), _mark_of(job.entry, "after")):
        job.entry["settled_unchanged"] = False
        return ""
    doubt = seen.frame_moved or seen.read_failed
    # PROVEN only when the settle took a fresh read nothing contradicts: the
    # learner never calls a tap dead otherwise.
    job.entry["settled_unchanged"] = not doubt and seen.screen is not screen
    return UNVERIFIED if doubt else NO_CHANGE


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
