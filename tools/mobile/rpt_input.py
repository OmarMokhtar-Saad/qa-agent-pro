from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True)
class _DocInput:
    """The keyword arguments of one page build, carried as a single value."""

    run_id: str
    manifest: dict
    cases: list
    screens: object
    lease: dict
    tally: dict
    partial: bool
    observations: object = None
    coverage: dict | None = None


# The one book a FRAME resolves against: every screen and every look at it.
#
# TWO libraries, joined once, HERE. ``screens`` answers which screens the run
# visited and stays the accessibility audit's input -- one row per screen, not
# one per look at it. Adding every stored OBSERVATION is what lets a four-turn
# conversation draw four pictures instead of one.
#
# Named rather than inlined because two consumers now need the SAME book: the
# page body resolves frames against it and :func:`_shots` funds pictures for
# it, and a picture funded against one book and resolved against another is a
# caption with no image under it.
#
# Merged rather than passed as a pair because the keys do not collide IN
# PRACTICE: ``perception._screen_id`` emits twelve hex characters and an
# observation id is that id plus a dash and up to twelve more, so no screen id
# has the shape of an observation key. NOT an invariant the code enforces:
# ``run_store._RUN_ID_RE`` accepts up to 64 characters of ``[A-Za-z0-9._-]``,
# so a STORED screen id of ``aabbccdd0011-000000000000`` is admissible and
# would collide, with the observation silently winning this ``update``.
# Nothing produces such an id today; if something ever does, the fix is two
# dictionaries rather than a longer comment. Stated because an invariant
# asserted in prose and unenforced in code is how a reader stops checking.
def _merged_library(screens: object, observations: object) -> dict:
    library = dict(screens if isinstance(screens, dict) else {})
    library.update(observations if isinstance(observations, dict) else {})
    return library
