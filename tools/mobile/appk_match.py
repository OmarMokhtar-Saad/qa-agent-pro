"""Matching saved notes against a replay step: resource id, env, activity."""

from __future__ import annotations

from typing import Any, NamedTuple

from tools.mobile import appk_consts


def rid_matches(have: Any, want: Any) -> bool:
    """An element's resource id against a note's: equal, or ``want`` is the id
    WITHOUT its package prefix (``resend`` never matches ``send``)."""
    have_n = " ".join(str(have or "").split()).lower()
    want_n = " ".join(str(want or "").split()).lower()
    if not have_n or not want_n:
        return False
    return have_n == want_n or have_n.endswith("/" + want_n)


def env_matches(when_env: Any, run_env: Any) -> bool:
    """True when a note's ``when.env`` applies to this run: every pair equals the
    run's declared env. An empty run env never satisfies a non-empty scope (fail
    closed); a note with no env scope always applies."""
    if not when_env:
        return True
    if not isinstance(when_env, dict) or not isinstance(run_env, dict) or not run_env:
        return False
    return all(k in run_env and str(run_env[k]) == str(v) for k, v in when_env.items())


def _env_applies(guard: dict, when_env: Any, env: Any) -> bool:
    """An env-scoped guard applies when the loader stamped it ``_env_ok`` or the
    run env satisfies its condition (an absent env never does)."""
    return bool(guard.get("_env_ok")) or env_matches(when_env, env)


def _guard_applies(guard: dict, op: str, target: tuple) -> bool:
    """One guard against the op; *target* is ``(have, env)``."""
    have, env = target
    when = guard.get("when") or {}
    if guard.get("kind", "wait") not in appk_consts.GUARD_KINDS:
        return False
    if when.get("env") and not _env_applies(guard, when["env"], env):
        return False
    if when.get("op") and when["op"] != op:
        return False
    if when.get("rid") and not any(rid_matches(r, when["rid"]) for r in have):
        return False
    return True


class GuardTarget(NamedTuple):
    """What a guard is matched against: the op, the target ids and the run env."""

    op: str
    rids: Any
    env: Any = None


def candidates(guards: Any, target: GuardTarget) -> list:
    """The guards that apply to this op on this target. *rids* is one resource
    id or every id the action could hit (its own ``rid`` plus the ids of the
    elements its text, label or id selects); a rid-scoped note matches when ANY
    of them does. An env-scoped note applies only when *env* (the run's declared
    env) matches it (``env_matches``) or the loader stamped it ``_env_ok``;
    precondition and fact notes are never guards."""
    rids = target.rids
    have = [rids] if isinstance(rids, str) else list(rids or [])
    pair = (have, target.env)
    return [g for g in list(guards or []) if _guard_applies(g, target.op, pair)]


def activity_ok(guard: dict, probe: Any) -> bool:
    """True when the note names no activity, or names one the focus probe shows.
    An unknown focus (``""``) never satisfies an activity-scoped note."""
    want = str(((guard or {}).get("when") or {}).get("activity") or "").strip()
    if not want:
        return True
    return want.lower() in str(probe or "").lower()
