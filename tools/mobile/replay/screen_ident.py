"""Replay screen identity: hashes, ids, texts and the per-action trace entry."""

from __future__ import annotations

import time
from typing import NamedTuple

from tools.mobile import actions as actions_mod

_wall_clock = time.time


def _screen_hash(screen: object) -> str:
    """``perception.prune``'s CONTENT hash for a screen, or ``""``.

    A DIFFERENT value from ``screen_id`` and deliberately named apart: the id
    is package + activity + the top three texts, which is what makes a scrolled
    list "the same screen" -- and what made every chat reply ``no_change``. The
    hash is seeded from every element. Two questions, two names.
    """
    body = screen if isinstance(screen, dict) else {}
    return str(body.get("hash") or "")


def _stamp_after(entry: dict, screen: object) -> None:
    """Stamp BOTH halves of the after-observation, in one call.

    ``after_screen_id`` says WHICH screen the step ended on; ``after_screen_hash``
    says WHICH LOOK at it. The report joins a step to its stored picture on the
    PAIR -- `report._obs_key` needs both, and on a chat screen the id alone is
    ambiguous because one screen id covers every turn of the conversation.

    ONE function because the two fields are one fact recorded at one instant,
    and recording them separately is not a style difference: measured on the
    tree that shipped v1.87.0, the id was written at THIRTEEN sites and the hash
    at ONE, so twelve of thirteen steps rendered "not captured" while their PNG
    sat on disk. A caller that stamps only one half is the same defect returning,
    which `tests/mobile/test_mobile_after_stamp.py` now fails by name.
    """
    entry["after_screen_id"] = _screen_id(screen)
    entry["after_screen_hash"] = _screen_hash(screen)


class EntrySpec(NamedTuple):
    position: int
    action: object
    before_id: str
    started: float
    before_hash: str = ""


# The CONTENT hashes, beside the ids. ``after_screen_hash`` is stamped
# only where the replay SETTLES, which is the one place that judges
# ``no_change``; entries stamped elsewhere leave it empty, because
# nothing reads it there and a half-filled field that looks general is
# how a sentinel gets trusted for something it does not carry.
# ``ms`` is stamped by ``_append``, AFTER the action ran. Computing it here read
# the clock at creation, so every real run carried ``ms: 0`` -- a
# 4000 ms wait included -- and the report could time nothing.
# ``at`` is the host wall clock at the moment the action BEGAN, the same clock the
# case checkpoint's `started`/`updated` use, so app-log evidence can be
# placed between actions by absolute time (plan mobile-app-evidence D6).
def _entry(spec: EntrySpec) -> dict:
    return {
        "index": int(spec.position),
        "action": actions_mod.redact_action(spec.action),
        "before_screen_id": str(spec.before_id or ""),
        "after_screen_id": "",
        "before_screen_hash": str(spec.before_hash or ""),
        "after_screen_hash": "",
        "ms": 0,
        "_started": float(spec.started),
        "at": _wall_clock(),
        "outcome": "",
        "detail": "",
    }


def _screen_id(screen: object) -> str:
    return (
        str((screen or {}).get("screen_id") or "") if isinstance(screen, dict) else ""
    )


def _screen_package(screen: object) -> str:
    return str((screen or {}).get("package") or "") if isinstance(screen, dict) else ""


def _texts(screen: object) -> set:
    """Every visible string on *screen*, whitespace-normalised.

    The unit ``new_text`` diffs. Element ids are positional and change on every
    dump, and bounds move when a list scrolls, so CONTENT is the only stable
    thing to compare two screens by.
    """
    out: set = set()
    if not isinstance(screen, dict):
        return out
    for element in screen.get("elements") or []:
        if not isinstance(element, dict):
            continue
        for field in ("text", "desc"):
            value = " ".join(str(element.get(field) or "").split())
            if value:
                out.add(value)
    return out
