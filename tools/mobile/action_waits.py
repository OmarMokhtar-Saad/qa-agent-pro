"""The wait arithmetic: what a script's ``wait`` actions may spend, in ms."""

from __future__ import annotations

from typing import Any

from tools.mobile.action_consts import (
    MAX_CONDITION_WAIT_MS,
    MAX_TOTAL_WAIT_MS,
    UNTIL_TEXT_DEFAULT_MS,
    WAIT_UNTIL_OPS,
)


def _bare_until_text_count(actions: Any) -> int:
    """How many ``wait`` actions in *actions* carry ``until_text`` and no
    explicit ``ms`` -- the ones :func:`total_wait_ms` no longer charges a flat
    :data:`UNTIL_TEXT_DEFAULT_MS` each, and :func:`bare_wait_ms_each` splits
    the remaining total across at replay time.
    """
    count = 0
    for action in list(actions or []):
        if str(getattr(action, "op", "") or "") != "wait":
            continue
        ms = int(getattr(action, "ms", 0) or 0)
        if not ms and (
            str(getattr(action, "until_text", "") or "").strip()
            or str(getattr(action, "until_rid", "") or "").strip()
        ):
            count += 1
    return count


def _explicit_wait_ms(actions: Any) -> int:
    """Device time every ``wait`` with an explicit ``ms`` may spend, in ms.
    Excludes bare ``until_text`` waits -- see :func:`_bare_until_text_count`.
    A condition wait counts its ``max_s``: the most it may spend.
    """
    total = 0
    for action in list(actions or []):
        op = str(getattr(action, "op", "") or "")
        if op in WAIT_UNTIL_OPS:
            total += int(float(getattr(action, "max_s", 0) or 0) * 1000)
        elif op == "wait":
            total += int(getattr(action, "ms", 0) or 0)
    return total


def bare_wait_ms_each(actions: object) -> int:
    """One bare ``until_text`` wait's share of what the total allows, in ms.

    The explicit-``ms`` waits are subtracted first, and what remains of
    :data:`MAX_TOTAL_WAIT_MS` is split evenly across every bare wait -- so N
    bare waits together never push the script's total over the cap, however
    large N is. Also never above :data:`UNTIL_TEXT_DEFAULT_MS` -- a single (or
    small-N) bare wait does not get an unnecessarily long individual poll
    deadline just because the total happens to allow it (round-2 fix: without
    this cap, one bare wait alone would poll for a full 25000ms instead of the
    previous, deliberately shorter, 20000ms default). ``0`` when there is no
    bare wait to split for.
    """
    bare = _bare_until_text_count(actions)
    if not bare:
        return 0
    remaining = MAX_TOTAL_WAIT_MS - _explicit_wait_ms(actions)
    return min(UNTIL_TEXT_DEFAULT_MS, max(1, remaining // bare))


def total_wait_ms(actions: object) -> int:
    """Device time every ``wait`` in *actions* may spend, in ms.

    A bare ``wait until_text`` (no explicit ``ms``) is charged its SPLIT share
    of :data:`MAX_TOTAL_WAIT_MS` -- see :func:`bare_wait_ms_each` -- rather than
    a flat :data:`UNTIL_TEXT_DEFAULT_MS` each, so any number of bare waits
    together are always within the total; only the ``ms`` a script sets
    explicitly can push it over.
    """
    explicit = _explicit_wait_ms(actions)
    bare = _bare_until_text_count(actions)
    if bare:
        explicit += bare * bare_wait_ms_each(actions)
    return explicit


def condition_wait_ms(actions: object) -> int:
    """The ``max_s`` ceilings of every condition wait in *actions*, in ms."""
    total = 0
    for action in list(actions or []):
        if str(getattr(action, "op", "") or "") in WAIT_UNTIL_OPS:
            total += int(float(getattr(action, "max_s", 0) or 0) * 1000)
    return total


def _over_cap(actions: object) -> tuple:
    """``(total, cap)`` of the first wait class over its cap, else ``(0, 0)``."""
    condition = condition_wait_ms(actions)
    fixed = total_wait_ms(actions) - condition
    if fixed > MAX_TOTAL_WAIT_MS:
        return fixed, MAX_TOTAL_WAIT_MS
    if condition > MAX_CONDITION_WAIT_MS:
        return condition, MAX_CONDITION_WAIT_MS
    return 0, 0


def wait_budget_error(actions: object) -> str:
    """Why *actions* spend too long waiting, or ``""`` when they fit.

    Fixed waits answer to :data:`MAX_TOTAL_WAIT_MS`; event-driven condition
    waits answer to the looser :data:`MAX_CONDITION_WAIT_MS`.
    """
    total, cap = _over_cap(actions)
    if not cap:
        return ""
    return (
        f"the waits in this script total {total} ms, over the {cap} ms one "
        "submit allows; set ms per wait explicitly, or split the case into "
        "shorter scripts -- the server hands the screen back between them"
    )
