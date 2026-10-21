"""App Tester UI: the apps earlier runs on this device already used.

A ``qa_mobile_test`` call without ``app=`` is answered from here, before any
launch: one remembered app is used, several resolve to the newest within the
memory's lifetime (said in the reply), a tie or none is asked.

Rows are keyed per APP (the package id they resolve to): a display label and a
package id that name one app are one entry, shown by its display label.
"""

from __future__ import annotations

import time
from typing import NamedTuple

from tools.mobile.install_source import InstallOutcome
from tools.mobile.install_sources import app_tester_ui_store as store
from tools.mobile.install_sources.app_tester_ui_funcs import _label_required

choices_shown = 5


class Remembered(NamedTuple):
    """One remembered app: its label, package id and last-used time."""

    label: str
    bundle: str
    at: float


def _is_id(label: str) -> bool:
    """A package-id-looking label (no spaces, dotted): the weaker alias of a name."""
    return " " not in label and label.count(".") >= 2


def _fresh_rows(serial: str) -> list[Remembered]:
    section = store._load().get("rows")
    section = section if isinstance(section, dict) else {}
    prefix = serial + "|"
    ttl = store.TTL_S["rows"] or 0
    rows = [
        Remembered(key[len(prefix) :], str((e or {}).get("v") or ""), at)
        for key, e in section.items()
        if key.startswith(prefix) and isinstance(e, dict)
        if time.time() - (at := float(e.get("at") or 0)) <= ttl
    ]
    return [r for r in rows if r.label]


def remembered_apps(serial: str) -> list[Remembered]:
    """One entry per app on *serial* (newest first), named by its display label."""
    best: dict = {}
    for row in _fresh_rows(serial):
        group = row.bundle or row.label
        old = best.get(group)
        newest = max(row.at, old.at if old else 0.0)
        keep = old if old and _prefers(old, row) else row
        best[group] = keep._replace(at=newest)
    return sorted(best.values(), key=lambda r: r.at, reverse=True)


def _prefers(old: Remembered, new: Remembered) -> bool:
    """Whether *old* is the better name of the app than *new*."""
    return (not _is_id(old.label), old.at) >= (not _is_id(new.label), new.at)


def remembered_labels(serial: str) -> list[str]:
    """Labels (newest app first) picked on *serial* within the memory's lifetime."""
    return [r.label for r in remembered_apps(serial)]


def newest_app(serial: str) -> Remembered | None:
    """The newest remembered app, or ``None`` for none or a tie at the top."""
    apps = remembered_apps(serial)
    if not apps or (len(apps) > 1 and apps[0].at == apps[1].at):
        return None
    return apps[0]


def default_label(serial: str) -> str:
    """The newest remembered app's label, else ``""`` (none, or a tie)."""
    found = newest_app(serial)
    return found.label if found else ""


def newest_note(serial: str) -> str:
    """The reply line saying which of several remembered apps was taken, or ``""``."""
    found = newest_app(serial)
    if not found or len(remembered_apps(serial)) < 2:
        return ""
    return (
        "No `app=` was given: using the most recently used app "
        + repr(found.label)
        + "."
    )


def refusal(serial: str, named: tuple = ()) -> InstallOutcome:
    """The fast "which app?" pick-list, listing the apps used before.

    *named*: quoted names the goal holds that are none of those apps (H17); said
    first, so the host asks the user instead of the server reusing the last app."""
    labels = remembered_labels(serial)[:choices_shown]
    base = _label_required()
    said = ""
    if named:
        said = " The goal names %s, which is none of the known apps." % "; ".join(
            repr(n) for n in named
        )
    listed = " Used on this device before: %s." % "; ".join(map(repr, labels))
    return InstallOutcome(
        False, base.code, base.message + said + (listed if labels else "")
    )
