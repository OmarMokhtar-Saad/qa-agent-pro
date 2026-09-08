"""Find an Android SDK and a Java runtime that already exist.

Downloading 2.2GB onto a machine that already has Android Studio is the single
worst first impression this lane can make, so location comes before
provisioning and the resolved paths are reported with their SOURCE -- a tester
who is told "using the SDK at ~/Library/Android/sdk (ANDROID_HOME)" can tell
immediately whether we picked the one they meant.

Never raises: every public function returns ``{"error", "content"}``.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from tools.mobile import paths, platform_info

logger = logging.getLogger(__name__)

#: Tool -> its directory relative to the SDK root.
TOOL_RELATIVE: dict[str, tuple[str, ...]] = {
    "adb": ("platform-tools",),
    "emulator": ("emulator",),
    "sdkmanager": ("cmdline-tools", "latest", "bin"),
    "avdmanager": ("cmdline-tools", "latest", "bin"),
}

#: These two are shell scripts, so they carry ``.bat`` on Windows rather than
#: ``.exe``. Getting this wrong is the classic Windows provisioning failure.
SCRIPT_TOOLS: frozenset[str] = frozenset({"sdkmanager", "avdmanager"})

TOOL_NAMES: tuple[str, ...] = ("adb", "emulator", "sdkmanager", "avdmanager")

#: WinGet unpacks a package into
#: ``%LOCALAPPDATA%\Microsoft\WinGet\Packages\<id>_<publisher-hash>\`` and puts
#: NOTHING on PATH. Our own qa-doctor tells a Windows tester to run
#: ``winget install --id Google.PlatformTools -e``, so without this the tool the
#: product asked for is one the product cannot find -- a tester had to go
#: hunting for it by hand. The publisher-hash segment is a GLOB: it is not
#: derivable, and it carries no version ordering either, which is what decides
#: the tie-break below.
WINGET_PACKAGE_GLOB = "Google.PlatformTools_*"

#: Package directories ONE ``locate_sdk()`` call may examine. This runs per adb
#: invocation (``adb.resolve_adb``), so the bound is paid on every call made on
#: a machine that has no adb yet -- which is the only machine that reaches it.
WINGET_MAX_PACKAGE_DIRS = 16


def _winget_adb_candidates() -> list[tuple[str, Path]]:
    """``(source label, adb path)`` for a WinGet-installed platform-tools.

    DETERMINISTIC, and the determinism is the whole claim: the matching package
    directories are sorted by casefolded path and the first with an adb in it
    wins. The publisher-hash segment carries no version, so "newest" is not
    derivable from the name and inventing an ordering would be a second
    producer of meaning; "the same one every run, named in the label" is what a
    tester can actually act on. Directory iteration order is NOT that -- it
    differs between machines and between runs.

    Both layouts are probed because which one WinGet writes cannot be verified
    off Windows: the zip's own ``platform-tools/`` folder, and a flat
    extraction. No ``winget`` process is ever spawned; this is a filesystem
    glob.
    """
    if not platform_info.is_windows():
        return []
    base_raw = (os.environ.get("LOCALAPPDATA") or "").strip()
    if not base_raw:
        return []
    base = Path(base_raw) / "Microsoft" / "WinGet" / "Packages"
    leaf = platform_info.exe("adb")
    try:
        matches = sorted(
            (item for item in base.glob(WINGET_PACKAGE_GLOB) if item.is_dir()),
            key=lambda item: str(item).casefold(),
        )[:WINGET_MAX_PACKAGE_DIRS]
    except OSError:
        return []
    out: list[tuple[str, Path]] = []
    for pkg in matches:
        for candidate in (pkg / "platform-tools" / leaf, pkg / leaf):
            try:
                if candidate.is_file():
                    out.append(("winget:" + pkg.name, candidate))
                    break
            except OSError:
                continue
    return out


def _with_winget_adb(candidate: dict) -> dict:
    """Fill a MISSING ``adb`` from WinGet, and change nothing else.

    ``sdk_root``, ``source`` and ``found`` keep describing the SDK. A WinGet
    platform-tools package is not one -- it has no emulator and no
    cmdline-tools -- and qa-doctor prints ``sdk_root`` as "Android SDK found
    at", so calling it a root would make that line false. What moves is
    ``tools["adb"]``, which every consumer reads as a PATH TO AN EXECUTABLE
    (``adb.resolve_adb`` and, through it, the whole device lane), plus
    ``missing``, which is derived from ``tools`` and must stay derived from it.
    ``adb_source`` states where the path came from.

    Called only where the selected candidate has NO adb, so a machine with a
    real SDK pays one dict lookup.
    """
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


def _candidate_sdk_roots() -> list[tuple[str, Path]]:
    """(source label, root) in priority order. Ours is deliberately LAST."""
    out: list[tuple[str, Path]] = []
    for var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        raw = (os.environ.get(var) or "").strip()
        if raw:
            out.append((var, Path(raw).expanduser()))
    if platform_info.is_windows():
        local = (os.environ.get("LOCALAPPDATA") or "").strip()
        if local:
            out.append(("LOCALAPPDATA", Path(local) / "Android" / "Sdk"))
        else:
            # LOCALAPPDATA is absent from a service or scheduled-task
            # environment and from a stripped shell; USERPROFILE survives both,
            # and Android Studio's default lives at the same place beneath it.
            profile = (os.environ.get("USERPROFILE") or "").strip()
            if profile:
                out.append(
                    (
                        "USERPROFILE",
                        Path(profile) / "AppData" / "Local" / "Android" / "Sdk",
                    )
                )
    else:
        out.append(
            ("android-studio-macos", Path.home() / "Library" / "Android" / "sdk")
        )
        out.append(("android-studio-home", Path.home() / "Android" / "Sdk"))
    out.append(("qa-agents-cache", paths.sub("sdk")))
    return out


def tool_path(root: Path, tool: str) -> Path:
    """Where *tool* lives under SDK *root* on this host."""
    rel = TOOL_RELATIVE[tool]
    leaf = (
        platform_info.script(tool) if tool in SCRIPT_TOOLS else platform_info.exe(tool)
    )
    return Path(root).joinpath(*rel, leaf)


def locate_sdk() -> dict:
    """The best available SDK.

    ``{sdk_root, source, tools: {name: path or ""}, missing: [...], found}``.
    A root with every tool present wins immediately; otherwise the root missing
    the FEWEST tools is returned, because provisioning into an existing
    half-installed SDK is cheaper and less surprising than building a second
    one beside it. ``found`` is False only when no candidate directory exists
    at all.
    """
    try:
        best: dict | None = None
        for source, root in _candidate_sdk_roots():
            try:
                if not root.is_dir():
                    continue
            except OSError:
                continue
            tools = {}
            for tool in TOOL_NAMES:
                found_path = tool_path(root, tool)
                try:
                    tools[tool] = str(found_path) if found_path.is_file() else ""
                except OSError:
                    tools[tool] = ""
            missing = sorted(name for name, value in tools.items() if not value)
            candidate = {
                "sdk_root": str(root),
                "source": source,
                "tools": tools,
                "missing": missing,
                "found": True,
            }
            if not missing:
                return {"error": None, "content": candidate}
            if best is None or len(missing) < len(best["missing"]):
                best = candidate
        if best is not None:
            return {"error": None, "content": _with_winget_adb(best)}
        return {
            "error": None,
            "content": _with_winget_adb(
                {
                    "sdk_root": "",
                    "source": "",
                    "tools": dict.fromkeys(TOOL_NAMES, ""),
                    "missing": sorted(TOOL_NAMES),
                    "found": False,
                }
            ),
        }
    except Exception as exc:
        logger.exception("mobile.sdk_locator.locate_sdk failed")
        return {"error": str(exc), "content": None}


def _candidate_java_paths() -> list[tuple[str, Path]]:
    leaf = platform_info.exe("java")
    out: list[tuple[str, Path]] = []
    home = (os.environ.get("JAVA_HOME") or "").strip()
    if home:
        out.append(("JAVA_HOME", Path(home).expanduser() / "bin" / leaf))
    if platform_info.is_macos():
        out.append(
            (
                "android-studio-jbr",
                Path("/Applications/Android Studio.app/Contents/jbr/Contents/Home/bin")
                / leaf,
            )
        )
    elif platform_info.is_windows():
        for var in ("ProgramFiles", "LOCALAPPDATA"):
            base = (os.environ.get(var) or "").strip()
            if base:
                out.append(
                    (
                        "android-studio-jbr",
                        Path(base)
                        / "Android"
                        / "Android Studio"
                        / "jbr"
                        / "bin"
                        / leaf,
                    )
                )
    out.append(("qa-agents-cache", paths.sub("jre") / "bin" / leaf))
    return out


def locate_java() -> dict:
    """``{java, source, found}`` for the first usable Java on this host.

    The order matters and is deliberate on this project's own machine:
    ``JAVA_HOME`` there is a JDK the Android build tools reject, but
    ``sdkmanager`` and ``avdmanager`` are far less fussy than AGP, so
    ``JAVA_HOME`` still comes first and Android Studio's bundled JBR is the
    fallback rather than the other way round. Nothing here reads a Java
    VERSION: this module reports what exists, and the caller that needs a
    specific version says so in its own message.
    """
    try:
        for source, candidate in _candidate_java_paths():
            try:
                if candidate.is_file():
                    return {
                        "error": None,
                        "content": {
                            "java": str(candidate),
                            "source": source,
                            "found": True,
                        },
                    }
            except OSError:
                continue
        on_path = shutil.which("java")
        if on_path:
            return {
                "error": None,
                "content": {"java": on_path, "source": "PATH", "found": True},
            }
        return {"error": None, "content": {"java": "", "source": "", "found": False}}
    except Exception as exc:
        logger.exception("mobile.sdk_locator.locate_java failed")
        return {"error": str(exc), "content": None}


def studio_present() -> bool:
    """True when Android Studio appears to be installed on this host.

    The provisioner uses this for ONE decision the user settled: SDK licences
    are auto-accepted only when Android Studio is ABSENT. Where Studio exists,
    the tester has their own licence state and we do not answer a legal prompt
    on their behalf.
    """
    try:
        if platform_info.is_macos():
            return Path("/Applications/Android Studio.app").is_dir()
        if platform_info.is_windows():
            for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
                base = (os.environ.get(var) or "").strip()
                if base and (Path(base) / "Android" / "Android Studio").is_dir():
                    return True
            return False
        return False
    except Exception:
        logger.info("mobile.sdk_locator: could not test for Android Studio")
        return False
