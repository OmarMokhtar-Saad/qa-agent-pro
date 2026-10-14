"""The per-app profile: every word the engine must never know.

A profile is a JSON data file. It names the package, where the app writes its own
event log on the device, which logcat tag it uses, and -- above all -- the regular
expressions of the app's prose grammar: how it writes a data-layer call, a model
request, a usage line, a turn boundary, a card push. The engine (``grammar.py``)
holds a stream NAME per pattern and the parse algorithm; the pattern TEXT comes from
here, compiled once. A missing or invalid pattern disables that one stream and is
listed under "Can this capture be trusted"; it never raises.

Where profiles live. ``tools/mobile_evidence/profiles/*.json`` in this private
repository (excluded from the public distribution), then the tester's own
``~/.qa-agents/mobile/profiles/*.json`` (``paths.sub("profiles")``). A dist install
therefore has no profile until its owner writes one, and every evidence section
says so. Selection is by EXACT package name.

This module knows no app. Its only literals are field names and stream names.
"""

from __future__ import annotations

import logging
from pathlib import Path

from config.settings_guard import never_raise
from tools.mobile import paths
from tools.mobile_evidence.profiles_compile import compile  # noqa: A001, F401
from tools.mobile_evidence.profiles_load import from_dict, load  # noqa: F401
from tools.mobile_evidence.profiles_model import (  # noqa: F401
    GENERIC_PROFILE_NAME,
    MAX_PROFILE_BYTES,
    PROFILES_DIR,
    REQUIRED_STREAMS,
    Profile,
    generic_profile,
)
from tools.process_env import home_dir

logger = logging.getLogger(__name__)


def _search_dirs(extra: list | None) -> list:
    dirs = [PROFILES_DIR]
    # the lane is always present in this tree
    with never_raise() as g:
        dirs.append(paths.sub("profiles"))
    if g.failed:
        dirs.append(home_dir() / ".qa-agents" / "mobile" / "profiles")
    for item in extra or []:
        dirs.append(Path(str(item)))
    return dirs


def available(search_dirs: list | None = None) -> list:
    """Every loadable profile, in search order. Unreadable files are skipped and logged."""
    found: list = []
    for directory in _search_dirs(search_dirs):
        try:
            candidates = sorted(directory.glob("*.json")) if directory.is_dir() else []
        except OSError:
            candidates = []
        for candidate in candidates:
            loaded = load(candidate)
            if loaded.get("error"):
                logger.info(
                    "mobile_evidence.profiles: skipped %s: %s",
                    candidate,
                    loaded["error"],
                )
                continue
            found.append(loaded["content"])
    return found


def profile_for(package: object, search_dirs: list | None = None) -> Profile | None:
    """The profile whose package equals *package* exactly, or None."""
    wanted = str(package or "").strip()
    if not wanted:
        return None
    for profile in available(search_dirs):
        if profile.package == wanted:
            return profile
    return None
