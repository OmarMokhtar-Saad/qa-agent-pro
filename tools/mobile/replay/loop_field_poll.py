"""Replay engine: an empty IME read-back is judged by the screen, polled (E-01).

After a ``clear`` the keyboard's one-shot read of the focused field can answer
"" for a field that did take the text (a search field re-filters async). The
dump is the poll: each good dump is checked for the app moving on AND for the
SAME field now holding the typed text (after a clear: any new text there), until
either is proved or the settle deadline passes. Never a fixed sleep; works for
any app.
"""

from __future__ import annotations

from tools.mobile.replay.context import Context
from tools.mobile.replay.loop_field_landed import Typed, shows_typed
from tools.mobile.replay.loop_field_moved import (
    Moved,
    ReadProbe,
    _deadline,
    _read_moved,
    _transient,
    _verdict,
)


async def moved_or_landed(ctx: Context, before: object, typed: Typed) -> Moved:
    """Poll dumps until the typed text shows in the same field or the app moved on.

    A landed read returns ``Moved(False, screen, True)``: settled, not moved.
    ``Moved(False)`` when neither was proved by the deadline.
    """

    def judge(seen: object, content: object, transient: bool) -> Moved | None:
        if not _transient(content) and shows_typed(content, typed, seen):
            return Moved(False, content, True)
        return _verdict(seen, content, transient)

    return await _read_moved(ctx, ReadProbe(before, judge), _deadline(ctx))
