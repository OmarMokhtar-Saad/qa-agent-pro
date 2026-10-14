"""Replay screen guards: the screen-level stops and the wording they travel with."""

from __future__ import annotations

import logging

from tools.mobile.providers import base as providers_base
from tools.mobile.replay.elements import element_label
from tools.mobile.replay.guard_consts import (
    GUARD_DETAIL_CONTROL_NOT_READ,
    GUARD_DETAIL_NOT_FULLY_SEEN,
    GUARD_DETAIL_SCREEN_GENERIC,
    SCREEN_CONTROL_NOT_READ,
    SCREEN_NOT_FULLY_SEEN,
    SCREEN_NOT_THE_APP,
)
from tools.mobile.replay.guard_lex import (
    _control_detail,
    destructive_hit,
    not_the_app_detail,
)

logger = logging.getLogger("tools.mobile.executor")


# Every screen-level guard sentinel, FROM THE REGISTRY THAT HANDLES THEM.
#
# THE ONE DERIVATION. Both consumers and the ratchet that grades them read
# this rather than each deriving the set again -- mirrored conditions drift.
#
# It used to scan ``globals()`` for names starting with ``SCREEN_``, and a
# review refuted that in one mutation: the authority on what a screen-level
# stop IS is not the spelling of a module constant, it is what
# :func:`screen_hit` returns. A stop named ``PACKET_ROOT_DROPPED`` -- a
# failure mode this module's own comments describe as measured and real --
# was invisible to the scan, so it reached the model with the control wording
# at BOTH consumers while 1813 tests stayed green.
#
# Registration is now the definition: a sentinel exists here because a
# handler exists for it. What keeps the PRODUCER honest is a separate,
# mechanical check -- ``test_every_screen_level_stop_screen_hit_can_return_
# is_registered`` reads the returns out of :func:`screen_hit` itself, so a
# stop with no row fails at authoring time whatever it is called.
def screen_sentinels() -> frozenset:
    """Every screen-level guard sentinel, FROM THE REGISTRY THAT HANDLES THEM."""
    return frozenset(screen_stop_details())


#: Sentinel -> the ``detail`` that stop carries, as ``(screen, expected)``.
#: A REGISTRY, not an if/elif chain: the chain's ``else`` is what silently gave
#: a new sentinel the control wording, and an unregistered key is visible here
#: (``screen_stop_details()`` vs :func:`screen_sentinels`) where a missing
#: branch in a chain is not. Adding a sentinel without a row fails the ratchet.
def screen_stop_details() -> dict:
    return {
        SCREEN_NOT_FULLY_SEEN: lambda screen, expected: GUARD_DETAIL_NOT_FULLY_SEEN,
        SCREEN_NOT_THE_APP: lambda screen, expected: not_the_app_detail(
            (screen or {}).get("package"), expected
        ),
        SCREEN_CONTROL_NOT_READ: lambda screen, expected: GUARD_DETAIL_CONTROL_NOT_READ,
    }


# The ``detail`` for ANY guard stop -- the ONE place that decides.
#
# A registered sentinel gets its own sentence. A term that IS a lexicon
# entry really is a control, so it keeps :data:`GUARD_DETAIL`. ANYTHING ELSE
# -- including an unregistered screen stop, whatever it is named and however
# it is phrased -- gets the generic screen sentence and an error log: saying
# something true and unhelpful beats saying something false and actionable.
#
# The middle test is IDENTITY with the lexicon, not containment; the
# containment form let a stop phrased "the app was reset between actions"
# read as a control. Identity needs the EMPTY guard explicitly, and the
# switch to it did not have one: ``destructive_hit("") != ""`` is False, so
# an empty term fell through to the control wording -- "a control that looks
# irreversible ()" -- where containment had given the generic sentence. A
# fail-safe whose degenerate input fails OPEN is the wrong way round, and
# this docstring claimed the opposite in the same commit that broke it. Never raises -- this is on the replay path, and a guard
# that crashes is a guard that is off.
def guard_detail(hit: str, screen: object, expected: object) -> str:
    """The ``detail`` for ANY guard stop -- the ONE place that decides."""
    try:
        handler = screen_stop_details().get(hit)
        if handler is not None:
            return handler(screen if isinstance(screen, dict) else {}, expected)
        if not hit or destructive_hit(hit) != hit:
            return _unrecognised_guard_term(hit)
    except Exception:  # pragma: no cover - defensive; the guard must not crash
        logger.error("mobile.executor.guard_detail failed", exc_info=True)
        return GUARD_DETAIL_SCREEN_GENERIC
    return _control_detail(hit)


# Log a guard term that is neither a screen stop nor a lexicon match.
# NOT "is it a known sentinel" -- that question cannot be asked
# honestly here, because registration now DEFINES the sentinel set,
# so the answer is yes exactly when a handler was found above.
# The question that survives is the one GUARD_DETAIL actually
# claims: is this a CONTROL? A term the lexicon does not match is
# not one, whatever it is called, so the control wording would be
# false and the generic screen sentence is what is true.
#
# IDENTITY, not containment. `not destructive_hit(hit)` was the
# first form and a review broke it in one line: a stop phrased "the
# app was reset between actions" CONTAINS the lexicon token "reset",
# so it read as a control and got the control wording. What arrives
# here from the guard is the lexicon TERM itself (see the call site
# -- `hit = destructive_hit(label)`), so a genuine term is its own
# match and a sentence that merely mentions one is not.
# This is the runtime half of the producer check in the ratchet: a
# screen-level stop nobody registered fails safe HERE even if it
# reached a shipped build.
# ERROR, not warning: a guard quietly degrading to a generic
# sentence is the exact failure mode this whole chain is about, and
# a warning in a replay log is a line nobody reads.
def _unrecognised_guard_term(hit: str) -> str:
    """Log a guard term that is neither a screen stop nor a lexicon match."""
    logger.error(
        "mobile.executor: guard term %r is neither a registered screen "
        "stop nor a lexicon match",
        hit,
    )
    return GUARD_DETAIL_SCREEN_GENERIC


# Notes on screen_hit's branches (kept here so the function stays short).
#
# THE FIDELITY QUESTION, and ONLY it. `judged` PRESENT selects the first branch
# and returns; ABSENT leaves everything after it byte-for-byte what it was. It
# is a parameter rather than a second function on purpose: the ratchet
# (test_screen_hit_is_the_only_screen_level_producer) reads the producers out
# of this module and a new one would be ungraded.
#
# DELEGATED, never re-derived. `providers_base.is_untrusted` is THE one answer
# to "may the guard judge what it read here", and it carries its own non-dict
# check, so spelling the lookup again would be the same answer derived twice
# from the same input -- mirrored conditions drift. ABSENT or UNKNOWN is NOT
# untrusted -- only `degraded` and `blind` are; a guard stop is terminal for a
# scripted case, so refusing on absence would stop every checkpoint written
# before the seam existed. That rule lives in `providers_base`, once.
#
# TRUNCATION IS ONE WAY THE PACKET CAN FAIL TO CARRY THE SCREEN, NOT THE ONLY
# ONE. `prune` takes its root bounds from the first node carrying them and
# drops everything outside; on a multi-window dump whose systemui window sorts
# first, the app's whole window is dropped and `truncated` stays False.
# Measured: one element kept (a clock), and the press guard cleared a screen
# holding "Confirm payment" that it had never seen. So a screen whose dominant
# package is not the app under test cannot be vouched for. An EMPTY
# `expected_package` means the caller did not say which app it is driving, and
# a refusal invented on no evidence would be its own defect -- so that case
# keeps today's answer.
# The lexicon term some element on *screen* carries in its OWN strings, a
# ``SCREEN_*`` sentinel, or ``""``.
#
# The sentinels are :data:`SCREEN_NOT_FULLY_SEEN`, when the packet is
# truncated, and :data:`SCREEN_NOT_THE_APP`, when the dominant package is not
# the app under test. Both are guard TERMS that travel on to the model, and
# each needs its own branch in every consumer that reads one -- pinned by
# ``tests/mobile/test_mobile_sentinel_ratchet.py``, which derives the list
# from this module. This docstring is a claim; that file is the check.
#
# What a ``press`` is judged against, because the control its key reaches is
# not in the dump. Judged ELEMENT BY ELEMENT, never as one joined string: a
# multi-word lexicon term matches a contiguous token run, and a join put
# ``CallLog`` beside "out of office" and read "log out" across the seam --
# the same false stop the actuated-node path avoids. Each element
# contributes ``element_label`` WITHOUT containment; a wrapper's contained
# text belongs to other elements, which are judged on their own turn. Never
# raises.
# Notes on each branch: the comment block above this function.
def screen_hit(
    screen: object, expected_package: str = "", judged: object = None
) -> str:
    """The guard term *screen* carries: a lexicon term, a sentinel, or ``""``."""
    if judged is not None:
        if providers_base.is_untrusted(judged):
            return SCREEN_CONTROL_NOT_READ
        return ""
    if not isinstance(screen, dict):
        return ""
    if screen.get("truncated"):
        return SCREEN_NOT_FULLY_SEEN
    seen = str(screen.get("package") or "")
    if expected_package and seen and seen != str(expected_package):
        return SCREEN_NOT_THE_APP
    for element in screen.get("elements") or []:
        hit = destructive_hit(element_label(element))
        if hit:
            return hit
    return ""
