"""App Tester UI: the app labels earlier runs on this device already used.

A ``qa_mobile_test`` call without ``app=`` is answered from here, before any
launch: the only remembered label is used, several are listed, none is asked.
"""

from __future__ import annotations

import time

from tools.mobile.install_source import InstallOutcome
from tools.mobile.install_sources import app_tester_ui_store as store
from tools.mobile.install_sources.app_tester_ui_funcs import _label_required

choices_shown = 5


def remembered_labels(serial: str) -> list[str]:
    """Labels (newest first) picked on *serial* within the memory's lifetime."""
    section = store._load().get("rows")
    section = section if isinstance(section, dict) else {}
    prefix = serial + "|"
    ttl = store.TTL_S["rows"] or 0
    fresh = [
        (float((e or {}).get("at") or 0), key[len(prefix) :])
        for key, e in section.items()
        if key.startswith(prefix) and isinstance(e, dict)
    ]
    live = [item for item in fresh if time.time() - item[0] <= ttl]
    return [label for _, label in sorted(live, reverse=True) if label]


def default_label(serial: str) -> str:
    """The one remembered label, else ``""`` (none, or ambiguous)."""
    labels = remembered_labels(serial)
    return labels[0] if len(labels) == 1 else ""


def refusal(serial: str) -> InstallOutcome:
    """The fast "which app?" refusal, listing the labels used before."""
    labels = remembered_labels(serial)[:choices_shown]
    base = _label_required()
    if not labels:
        return base
    listed = "; ".join(repr(label) for label in labels)
    return InstallOutcome(
        False, base.code, base.message + " Used on this device before: " + listed + "."
    )
