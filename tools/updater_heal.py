"""Updater manifest-heal marker and retry deferral (split from tools/updater.py)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import cast

from tools.updater_log import logger
from tools.updater_manifest import load_manifest
from tools.updater_paths import _MANIFEST_NAME

# SECONDS a manifest-only heal (MANIFEST.sha256 missing or empty) is not retried
# after an attempt, finished or not. A release that ships no manifest can never
# restore it, and the launcher checks every 15 minutes: without a floor that is a
# full archive download per check, forever.
MANIFEST_HEAL_RETRY_INTERVAL_S = 6 * 3600

# BYTES read from the heal marker; a larger file reads as "no attempt".
_HEAL_MARKER_MAX_BYTES = 4096


def _heal_marker(install_dir: Path) -> Path:
    """``data`` is a PROTECTED path, so a swap never overwrites or prunes this."""
    return install_dir / "data" / ".manifest-heal.json"


# The last manifest-heal attempt as a dict, or {} for a missing, unreadable or
# mis-shaped marker. Never raises: a bad marker must read as "no attempt".
def _read_heal_marker(install_dir: Path) -> dict:
    """The last manifest-heal attempt as a dict, or {} for a bad marker.

    Never raises: a missing, unreadable or mis-shaped marker reads as "no attempt".
    """
    try:
        with _heal_marker(install_dir).open("rb") as fh:
            raw = fh.read(_HEAL_MARKER_MAX_BYTES + 1)
        if len(raw) > _HEAL_MARKER_MAX_BYTES:
            return {}
        doc = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, RecursionError):
        return {}
    return doc if isinstance(doc, dict) else {}


# Record a heal attempt for ``remote`` (``attempted`` | ``healed`` |
# ``no-manifest``). Best effort: an unwritable data directory must not break the
# update check, it only costs a retry.
def _write_heal_marker(install_dir: Path, remote: object, outcome: str) -> None:
    """Record a heal attempt for ``remote`` (``attempted``/``healed``/``no-manifest``).

    Best effort: an unwritable data directory only costs a retry.
    """
    marker = _heal_marker(install_dir)
    doc = {"version": str(remote).lstrip("vV"), "outcome": outcome, "at": time.time()}
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps(doc), encoding="utf-8")
    except OSError as exc:
        logger.debug("Could not record the manifest-heal marker (%s).", exc)


# ``healed`` when the manifest is usable again after a heal, else
# ``no-manifest``: the release ships none, so a heal cannot restore it.
def _healed_outcome(install_dir: Path) -> str:
    """``healed`` when the manifest is usable again after a heal, else ``no-manifest``."""
    return "healed" if load_manifest(install_dir) else "no-manifest"


# True when the ONLY problem is the manifest itself and a heal from this release
# was already tried inside the floor, whether it failed or found that the release
# ships no manifest. Both are time-bound: with the manifest gone no other tamper is
# visible, so no marker may switch the heal off for good. A real file mismatch is
# never deferred, and a finished heal (``healed``) is not a reason to wait: a
# manifest deleted again after it is worth healing at once.
def _manifest_heal_deferred(
    install_dir: Path, mismatched: list, remote: object
) -> bool:
    """True when only the manifest is wrong and a heal was already tried in the floor."""
    if mismatched != [_MANIFEST_NAME]:
        return False
    doc = _read_heal_marker(install_dir)
    if doc.get("version") != str(remote).lstrip("vV"):
        return False
    at = doc.get("at")
    if doc.get("outcome") not in ("attempted", "no-manifest"):
        return False
    if type(at) not in (int, float):
        return False
    return 0 <= time.time() - cast("float", at) < MANIFEST_HEAL_RETRY_INTERVAL_S
