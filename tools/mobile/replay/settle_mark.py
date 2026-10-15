"""Replay settle: the value types a settle passes around and the unchanged test."""

from __future__ import annotations

from typing import Any, NamedTuple


class ScreenMark(NamedTuple):
    """A screen's content hash and its screen id (either may be empty)."""

    content_hash: str
    screen_id: str


class SettleJob(NamedTuple):
    """What a settle acts on: the run's context, the trace entry, the trace, the step."""

    ctx: Any
    entry: dict
    trace: list
    step_no: int


class SettleOpts(NamedTuple):
    """How a settle behaves: re-dump or not, whether to stamp no_change, the op."""

    redump: bool
    mark_no_change: bool
    op: str = ""
    extra_expected: tuple = ()
    before_change_wait: bool = False
    next_target: Any = None


# Whether an action left the screen's CONTENT as it was.
#
# Judged on the content hash when BOTH ends have one. ``screen_id`` is the
# wrong instrument and was the one used: it is seeded from package, activity
# and the top THREE texts (``perception._screen_id``), deliberately, so a
# scrolled list stays the same screen -- and so a chat that received a whole
# new assistant reply reported ``no_change``. Measured on every send of
# ``mrun-20260907-174354-758700``, including the one the model then proved
# with an ``assert_pass``.
#
# With either hash missing the ID comparison is KEPT, which is exactly
# today's behaviour. ``no_change`` is what tells a model its tap did nothing,
# and dropping the signal on a screen that carries no hash would replace an
# occasionally coarse answer with no answer at all.
#
# ``assert screen_changed`` still reads the ID and stays documented as WEAK
# (``_evaluate_assert``): two consumers want two different questions answered,
# so they get two values with two names, not one value with a caveat. The
# destructive guard reads the ID and the elements too, and neither moves.
def _unchanged(before: ScreenMark, after: ScreenMark) -> bool:
    """Whether an action left the screen's CONTENT as it was."""
    if before.content_hash and after.content_hash:
        return before.content_hash == after.content_hash
    return str(after.screen_id) == str(before.screen_id)
