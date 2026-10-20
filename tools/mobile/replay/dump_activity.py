"""Replay activity state: which dump path a device is on and what its last dump said about focus.

WHY THIS EXISTS (fix/1510-b). The qa-ime accessibility tree carries only the
root ``package`` (QaA11yService.kt), never the activity, and the old code paid
one ``dumpsys window | grep mCurrentFocus`` (~100 ms) beside EVERY dump and
every focus probe. Now:

* a dump on a tree source (qa-ime) makes NO adb call for the activity. Its
  focus is the dump's own package; the activity is resolved ONCE per replay
  step, after the settle (`dump_stamp.stamp_step`), always -- same-package
  navigation is the common case, so a changed package is not the trigger;
* the uiautomator fallback keeps one ``resolve_activity`` per dump;
* between two step stamps a tree-path dump is hashed with the activity the
  last step resolved, but only while the package is the one it was resolved
  for, so ``screen_id`` (package|activity|texts[:3]) stays stable across
  polls and a navigation to another package never wears a stale activity.
"""

from __future__ import annotations

from typing import Any

from tools.mobile import dump_reuse, tree_source

_SOURCE: dict[str, str] = {}
_LAST: dict[str, tuple] = {}
_MEMO: dict[str, tuple] = {}


def _bounded(table: dict) -> None:
    while len(table) > dump_reuse.MAX_HELD_SERIALS:
        del table[next(iter(table))]


def _put(table: dict, serial: str, value: Any) -> None:
    key = str(serial or "")
    table.pop(key, None)
    table[key] = value
    _bounded(table)


def note_source(serial: str, name: str) -> None:
    """Record which tree source served the latest dump of *serial*."""
    _put(_SOURCE, serial, str(name or ""))


def tree_path(serial: str) -> bool:
    """True when the device's latest dump came from a tree source (no activity in it)."""
    name = _SOURCE.get(str(serial or ""), tree_source.FALLBACK_SOURCE)
    return name not in ("", tree_source.FALLBACK_SOURCE)


def note_dump(serial: str, raw: Any, seen: tuple) -> None:
    """Keep the latest dump's raw tree and ``(view, package)`` for a later re-stamp."""
    _put(_LAST, serial, (raw, seen))


def last_package(serial: str) -> str:
    """The package of the device's latest dump, ``""`` when none was read."""
    held = _LAST.get(str(serial or ""))
    return str(held[1][1] or "") if held else ""


def last_raw(serial: str) -> tuple:
    """``(raw, view)`` of the latest dump, ``(None, None)`` when none was read."""
    held = _LAST.get(str(serial or ""))
    return (held[0], held[1][0]) if held else (None, None)


def remember(serial: str, package: str, activity: str) -> None:
    """Keep the activity a step resolved, with the package it was resolved for."""
    _put(_MEMO, serial, (str(package or ""), str(activity or "")))


def memo_activity(serial: str, package: str) -> str:
    """The last resolved activity while *package* is still the one it belongs to."""
    held = _MEMO.get(str(serial or ""))
    if held and package and held[0] == str(package):
        return held[1]
    return ""


def hint(serial: str) -> str:
    """Best no-adb focus: the remembered ``package/Activity``, else the package."""
    package = last_package(serial)
    return memo_activity(serial, package) or package


def memo(serial: str) -> tuple:
    """``(package, activity)`` the last step resolved, ``("", "")`` when none."""
    return _MEMO.get(str(serial or ""), ("", ""))
