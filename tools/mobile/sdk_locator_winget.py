"""WinGet-installed platform-tools lookup for :mod:`tools.mobile.sdk_locator`."""

from __future__ import annotations

from pathlib import Path

from tools.mobile import platform_info
from tools.process_env import get_env_str

# WinGet unpacks a package into
# ``%LOCALAPPDATA%\Microsoft\WinGet\Packages\<id>_<publisher-hash>\`` and puts
# NOTHING on PATH. The publisher-hash segment is a GLOB: it is not derivable
# and it carries no version ordering, which is what decides the tie-break.
WINGET_PACKAGE_GLOB = "Google.PlatformTools_*"


def _packages(base: Path, limit: int) -> list[Path]:
    try:
        return sorted(
            (item for item in base.glob(WINGET_PACKAGE_GLOB) if item.is_dir()),
            key=lambda item: str(item).casefold(),
        )[:limit]
    except OSError:
        return []


def _pkg_adb(pkg: Path, leaf: str) -> tuple[str, Path] | None:
    for candidate in (pkg / "platform-tools" / leaf, pkg / leaf):
        try:
            if candidate.is_file():
                return ("winget:" + pkg.name, candidate)
        except OSError:
            continue
    return None


# DETERMINISTIC: the matching package directories are sorted by casefolded
# path and the first with an adb in it wins. The hash segment carries no
# version, so "newest" is not derivable; "the same one every run, named in the
# label" is what a tester can act on. Both layouts are probed (the zip's own
# ``platform-tools/`` folder and a flat extraction). No ``winget`` process is
# ever spawned; this is a filesystem glob.
def winget_adb_candidates(limit: int) -> list[tuple[str, Path]]:
    """``(source label, adb path)`` for a WinGet-installed platform-tools."""
    if not platform_info.is_windows():
        return []
    base_raw = get_env_str("LOCALAPPDATA")
    if not base_raw:
        return []
    base = Path(base_raw) / "Microsoft" / "WinGet" / "Packages"
    leaf = platform_info.exe("adb")
    found = (_pkg_adb(pkg, leaf) for pkg in _packages(base, limit))
    return [hit for hit in found if hit is not None]
