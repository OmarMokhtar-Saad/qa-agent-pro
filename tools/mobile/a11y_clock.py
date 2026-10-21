"""Confirming a repeated a11y tree with the service's event clock, not a second dump.

A tree that repeats across an actuation is normally proven by one uiautomator dump
(:mod:`tools.mobile.a11y_fresh`). qa-ime 1.2.1 also tells how many window/content
events its service has heard since it connected (:mod:`tools.mobile.a11y_events`).
When the digest is identical AND that count did not move since the tree was first
served, nothing happened on screen the service could hear, so the repeat is real
and the confirming dump is skipped (note ``a11y_repeat_by_clock``).

The skip is allowed only when all of these hold, because a deaf service (the
8f3169 stale run) also reports an unchanged count:

* this serial's count already advanced across an earlier screen change, which proves
  the service hears events at all (``proven``);
* at most :data:`MAX_SKIPS` repeats in a row are taken on the clock, and as many
  on heard events; the next one runs the uiautomator confirm again. A clock with
  the every-event generation (qa-ime 1.2.2+) needs no ``proven`` but is capped
  too, at :data:`GENERATION_SKIPS`;
* the clock is that of the dump just taken. A reply without a clock (qa-ime older
  than 1.2.1) clears the state, so those runs keep the uiautomator confirm.

The count is per service connection, not monotonic: a smaller count means a new
connection, which is a change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from tools.mobile import a11y_events, a11y_unchecked, step_timing

#: Consecutive repeats the clock may confirm before uiautomator checks again.
MAX_SKIPS = 2
#: Repeats the every-event generation may confirm in a row; then uiautomator checks
#: again (a net for a service that stopped counting). Larger than :data:`MAX_SKIPS`.
GENERATION_SKIPS = 6
#: The ledger note for a repeat the clock confirmed.
BY_CLOCK_NOTE = "a11y_repeat_by_clock"
#: The ledger note for a repeat proven by events heard after the action.
BY_EVENT_NOTE = "a11y_repeat_by_event"
#: Device ms with no event after which a service that heard the action has rested.
EVENT_QUIET_MS = 200

Confirm = Callable[[], Awaitable["dict | None"]]


@dataclass
class _Seen:
    digest: str
    count: int
    proven: bool
    skips: int = 0
    #: Repeats taken on heard events; ``served`` never resets it, only a confirm.
    heard: int = 0
    #: The every-event counter when the tree was served (qa-ime 1.2.2+).
    generation: int | None = None


_SEEN: dict[str, _Seen] = {}


def forget(serial: str) -> None:
    """Drop the stored count and the proof (an enable, a restore, a stale verdict)."""
    _SEEN.pop(str(serial), None)


def no_event_since(serial: str, digest: str) -> bool:
    """True when the service heard no event since *digest* was served.

    A repeated tree with no event is "unchanged", not stale: no confirming dump.
    Only a clock with the every-event generation (qa-ime 1.2.2+) is trusted
    outright, up to :data:`GENERATION_SKIPS` repeats in a row: the window/content
    count alone is also flat on a deaf service, so without it the proven, capped
    rule of qa-ime 1.2.1 applies.
    """
    seen, clock = _SEEN.get(serial), a11y_events.latest(serial)
    if seen is None or clock is None or seen.digest != digest:
        return False
    if clock.generation is None:
        return seen.proven and seen.skips < MAX_SKIPS and clock.count == seen.count
    return (
        clock.count == seen.count
        and clock.generation == seen.generation
        and seen.skips < GENERATION_SKIPS
    )


def _heard_after(serial: str, digest: str) -> bool:
    """True when a proven service heard events since the tree was served, then rested.

    The action reached the service (events newer than the last served tree
    arrived) and the screen went quiet, so the repeat is the settled screen,
    not a deaf service replaying an old frame.
    """
    seen, clock = _SEEN.get(serial), a11y_events.latest(serial)
    if seen is None or clock is None or seen.digest != digest or not seen.proven:
        return False
    if seen.heard >= MAX_SKIPS:
        return False
    return clock.count > seen.count and clock.quiet_ms() >= EVENT_QUIET_MS


async def judged(serial: str, digest: str, confirm: Confirm) -> "dict | None":
    """``None`` when the clock proves the repeat, else the result of *confirm*."""
    if no_event_since(serial, digest):
        _SEEN[serial].skips += 1
        step_timing.note(BY_CLOCK_NOTE)
        return None
    if _heard_after(serial, digest):
        _SEEN[serial].heard += 1
        step_timing.note(BY_EVENT_NOTE)
        return None
    # H04: a blind reuse is the last resort, after the event clock had its say.
    if a11y_unchecked.reuse(serial, digest):
        return None
    refused = await confirm()
    seen = _SEEN.get(serial)
    if refused is None and seen is not None:
        seen.skips = seen.heard = 0
    return refused


def served(serial: str, digest: str) -> None:
    """Remember the count of the tree just served; no clock forgets everything."""
    clock, seen = a11y_events.latest(serial), _SEEN.get(serial)
    if clock is None:
        forget(serial)
    elif seen is None or seen.digest != digest:
        advanced = seen is not None and clock.count > seen.count
        _SEEN[serial] = _Seen(
            digest,
            clock.count,
            bool(advanced or (seen and seen.proven)),
            generation=clock.generation,
        )
    elif clock.count != seen.count:
        seen.count, seen.skips = clock.count, 0
        seen.generation = clock.generation
    elif clock.generation != seen.generation:
        seen.generation = clock.generation  # count flat: skips keep counting
