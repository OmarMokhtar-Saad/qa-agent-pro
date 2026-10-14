"""Find an Android SDK and a Java runtime that already exist.

Downloading 2.2GB onto a machine that already has Android Studio is the single
worst first impression this lane can make, so location comes before
provisioning and the resolved paths are reported with their SOURCE.

Never raises: every public function returns ``{"error", "content"}``.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from tools.mobile import paths, platform_info  # noqa: F401
from tools.mobile import sdk_locator_cand as _cand
from tools.mobile.sdk_locator_avd import (  # noqa: F401
    _AVD_DISPLAY_KEYS,
    AVD_CONFIG_MAX_BYTES,
    _avd_config_texts,
    _avd_name_is_safe,
    avd_display,
    avd_name_is_safe,
    avd_system_image,
    studio_present,
)
from tools.mobile.sdk_locator_cand import (  # noqa: F401
    SCRIPT_TOOLS,
    TOOL_NAMES,
    TOOL_RELATIVE,
    empty_sdk,
    select_sdk,
)
from tools.mobile.sdk_locator_winget import (  # noqa: F401
    WINGET_PACKAGE_GLOB,
    winget_adb_candidates,
)
from tools.process_env import get_env_str, home_dir  # noqa: F401

logger = logging.getLogger(__name__)

#: Package directories ONE ``locate_sdk()`` call may examine. This runs per adb
#: invocation (``adb.resolve_adb``), so the bound is paid on every call made on
#: a machine that has no adb yet -- which is the only machine that reaches it.
WINGET_MAX_PACKAGE_DIRS = 16


def _winget_adb_candidates() -> list[tuple[str, Path]]:
    """``(source label, adb path)`` for a WinGet-installed platform-tools."""
    return winget_adb_candidates(WINGET_MAX_PACKAGE_DIRS)


# Fill a MISSING ``adb`` from WinGet, and change nothing else. ``sdk_root``,
# ``source`` and ``found`` keep describing the SDK: a WinGet platform-tools
# package is not one, and qa-doctor prints ``sdk_root`` as "Android SDK found
# at". What moves is ``tools["adb"]`` (a PATH TO AN EXECUTABLE) plus
# ``missing``, which stays derived from ``tools``; ``adb_source`` says where
# the path came from. Called only where the selected candidate has NO adb.
def _with_winget_adb(candidate: dict) -> dict:
    """*candidate* with ``tools["adb"]`` filled from WinGet when it is missing."""
    tools = dict(candidate.get("tools") or {})
    if tools.get("adb"):
        return candidate
    found = _winget_adb_candidates()
    if not found:
        return candidate
    label, path = found[0]
    tools["adb"] = str(path)
    out = dict(candidate)
    out["tools"] = tools
    out["adb_source"] = label
    out["missing"] = sorted(name for name, value in tools.items() if not value)
    return out


def candidate_sdk_roots() -> list[tuple[str, Path]]:
    """(source label, root) in priority order. Ours is deliberately LAST."""
    return _cand.sdk_roots()


def tool_path(root: Path, tool: str) -> Path:
    """Where *tool* lives under SDK *root* on this host."""
    return _cand.tool_path(root, tool)


def _sdk_candidate(source: str, root: Path) -> dict | None:
    """What SDK tools *root* holds, or None when *root* is not a directory."""
    return _cand.sdk_candidate(source, root, tool_path)


# ``{sdk_root, source, tools: {name: path or ""}, missing: [...], found}``.
# A root with every tool present wins immediately; otherwise the root missing
# the FEWEST tools is returned. ``found`` is False only when no candidate
# directory exists at all.
def locate_sdk() -> dict:
    """The best available SDK."""
    try:
        roots = candidate_sdk_roots()
        best, complete = select_sdk(_sdk_candidate(s, r) for s, r in roots)
        if complete:
            return {"error": None, "content": best}
        return {"error": None, "content": _with_winget_adb(best or empty_sdk())}
    except Exception as exc:
        logger.exception("mobile.sdk_locator.locate_sdk failed")
        return {"error": str(exc), "content": None}


# Lives here, not in ``tools.mobile.adb``, because ``tools.mobile.adb`` imports
# ``tools.device_manager``: ``device_manager`` and qa-doctor read this one
# function without closing that import loop. ``tools.mobile.adb`` re-exports it.
def resolve_adb() -> str:
    """Absolute path of the adb to use, or the bare name as a last resort."""
    located = (locate_sdk() or {}).get("content") or {}
    found = str((located.get("tools") or {}).get("adb") or "")
    if found:
        return found
    return platform_info.exe("adb")


def candidate_java_paths() -> list[tuple[str, Path]]:
    return _cand.java_paths()


def locate_java() -> dict:
    """``{java, source, found}`` for the first usable Java on this host."""
    try:
        content = _cand.java_content(candidate_java_paths(), shutil.which)
        return {"error": None, "content": content}
    except Exception as exc:
        logger.exception("mobile.sdk_locator.locate_java failed")
        return {"error": str(exc), "content": None}
