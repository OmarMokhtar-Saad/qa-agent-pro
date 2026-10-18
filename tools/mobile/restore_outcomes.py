"""Record the IME / animation / a11y restore outcomes in ``manifest["restore"]``.

A restore that failed leaves the tester's device changed; the manifest is where
a reader of the run looks, so each outcome is stored there. A run with no
manifest (a placeholder owner) writes nothing. Never raises.
"""

from __future__ import annotations

import logging

from config.settings_guard import never_raise
from tools.mobile import run_manifest

logger = logging.getLogger("tools.mobile.restore_outcomes")


def _outcome(slot: object) -> dict:
    return dict(slot) if isinstance(slot, dict) else {}


def record(run_id: str, restores: tuple) -> None:
    """Store ``(ime, anim, a11y)`` under ``manifest["restore"]``."""
    with never_raise(logger, "restore outcomes not recorded", exc_info=True):
        manifest = run_manifest.read_manifest(run_id).get("content")
        if not isinstance(manifest, dict):
            return
        ime, anim, a11y = restores
        before = _outcome(manifest.get("restore"))
        now = {"ime": ime, "animations": anim, "a11y": a11y}
        manifest["restore"] = {
            k: _outcome(v) or _outcome(before.get(k)) for k, v in now.items()
        }
        run_manifest.write_manifest(run_id, manifest)
