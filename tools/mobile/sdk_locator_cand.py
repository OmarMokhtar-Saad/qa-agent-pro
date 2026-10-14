"""SDK-root and Java candidate discovery for :mod:`tools.mobile.sdk_locator`."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from tools.mobile import paths, platform_info
from tools.process_env import get_env_str, home_dir

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

Roots = list[tuple[str, Path]]


def _env_roots() -> Roots:
    out: Roots = []
    for var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        raw = get_env_str(var)
        if raw:
            out.append((var, Path(raw).expanduser()))
    return out


def _windows_roots() -> Roots:
    local = get_env_str("LOCALAPPDATA")
    if local:
        return [("LOCALAPPDATA", Path(local) / "Android" / "Sdk")]
    # LOCALAPPDATA is absent from a service or scheduled-task environment and
    # from a stripped shell; USERPROFILE survives both, and Android Studio's
    # default lives at the same place beneath it.
    profile = get_env_str("USERPROFILE")
    if not profile:
        return []
    return [("USERPROFILE", Path(profile) / "AppData" / "Local" / "Android" / "Sdk")]


def _posix_roots() -> Roots:
    return [
        ("android-studio-macos", home_dir() / "Library" / "Android" / "sdk"),
        ("android-studio-home", home_dir() / "Android" / "Sdk"),
    ]


def sdk_roots() -> Roots:
    """(source label, root) in priority order. Ours is deliberately LAST."""
    out = _env_roots()
    out.extend(_windows_roots() if platform_info.is_windows() else _posix_roots())
    out.append(("qa-agents-cache", paths.sub("sdk")))
    return out


def tool_path(root: Path, tool: str) -> Path:
    """Where *tool* lives under SDK *root* on this host."""
    rel = TOOL_RELATIVE[tool]
    leaf = (
        platform_info.script(tool) if tool in SCRIPT_TOOLS else platform_info.exe(tool)
    )
    return Path(root).joinpath(*rel, leaf)


def _tool_entry(path: Path) -> str:
    try:
        return str(path) if path.is_file() else ""
    except OSError:
        return ""


def sdk_candidate(
    source: str, root: Path, path_of: Callable[[Path, str], Path]
) -> dict | None:
    """What SDK tools *root* holds, or None when *root* is not a directory."""
    try:
        if not root.is_dir():
            return None
    except OSError:
        return None
    tools = {tool: _tool_entry(path_of(root, tool)) for tool in TOOL_NAMES}
    missing = sorted(name for name, value in tools.items() if not value)
    return {
        "sdk_root": str(root),
        "source": source,
        "tools": tools,
        "missing": missing,
        "found": True,
    }


# A root with every tool present wins immediately; otherwise the root missing
# the FEWEST tools is returned, because provisioning into an existing
# half-installed SDK is cheaper than building a second one beside it.
def select_sdk(candidates: Iterable[dict | None]) -> tuple[dict | None, bool]:
    """``(best candidate or None, complete)``; stops at the first complete one."""
    best: dict | None = None
    for candidate in candidates:
        if candidate is None:
            continue
        missing = candidate["missing"]
        if not missing:
            return candidate, True
        if best is None or len(missing) < len(best["missing"]):
            best = candidate
    return best, False


def empty_sdk() -> dict:
    """The candidate reported when no SDK directory exists at all."""
    return {
        "sdk_root": "",
        "source": "",
        "tools": dict.fromkeys(TOOL_NAMES, ""),
        "missing": sorted(TOOL_NAMES),
        "found": False,
    }


def _studio_jbr() -> Roots:
    if platform_info.is_macos():
        jbr = "/Applications/Android Studio.app/Contents/jbr/Contents/Home/bin"
        return [("android-studio-jbr", Path(jbr))]
    if not platform_info.is_windows():
        return []
    out: Roots = []
    for var in ("ProgramFiles", "LOCALAPPDATA"):
        base = get_env_str(var)
        if base:
            win_jbr = Path(base) / "Android" / "Android Studio" / "jbr" / "bin"
            out.append(("android-studio-jbr", win_jbr))
    return out


def java_paths() -> Roots:
    """(source label, java path) in priority order."""
    leaf = platform_info.exe("java")
    out: Roots = []
    home = get_env_str("JAVA_HOME")
    if home:
        out.append(("JAVA_HOME", Path(home).expanduser() / "bin" / leaf))
    out.extend((source, jbr / leaf) for source, jbr in _studio_jbr())
    out.append(("qa-agents-cache", paths.sub("jre") / "bin" / leaf))
    return out


def first_file(pairs: Roots) -> tuple[str, Path] | None:
    """The first ``(source, path)`` whose path is a file, else None."""
    for source, candidate in pairs:
        try:
            if candidate.is_file():
                return source, candidate
        except OSError:
            continue
    return None


# ``JAVA_HOME`` comes first and Android Studio's bundled JBR is the fallback:
# ``sdkmanager``/``avdmanager`` are far less fussy than AGP. Nothing here reads
# a Java VERSION; the caller that needs one says so in its own message.
def java_content(pairs: Roots, which: Callable[[str], Any]) -> dict:
    """``{java, source, found}`` for the first usable Java."""
    hit = first_file(pairs)
    if hit is not None:
        return {"java": str(hit[1]), "source": hit[0], "found": True}
    on_path = which("java")
    if on_path:
        return {"java": on_path, "source": "PATH", "found": True}
    return {"java": "", "source": "", "found": False}
