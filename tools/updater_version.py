"""Updater version parsing and comparison (split from tools/updater.py)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from tools.updater_log import logger


def _parse_version(value: str) -> Optional[tuple]:
    """Parse a ``X.Y.Z`` (optionally ``v``-prefixed) version to an int tuple.

    Returns ``None`` for anything without at least one numeric component, so a
    malformed version is treated as "unknown" (never newer) rather than raising.
    """
    v = (value or "").strip().lstrip("vV")
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)", v)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    nums = re.findall(r"\d+", v)
    if not nums:
        return None
    parts = [int(x) for x in nums[:3]]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_newer(remote: str, local: Optional[str]) -> bool:
    """True only when ``remote`` parses to a strictly greater version than
    ``local``. Any unparseable side yields ``False`` (safe: no update)."""
    r = _parse_version(remote or "")
    lo = _parse_version(local or "")
    if r is None or lo is None:
        return False
    return r > lo


def _local_version(install_dir: Path) -> Optional[str]:
    """Read the installed version from ``pyproject.toml`` (``version = "..."``),
    falling back to a ``VERSION`` file. Returns ``None`` if neither is readable —
    the caller then skips the update (can't compare safely). Never raises."""
    try:
        pyproject = install_dir / "pyproject.toml"
        if pyproject.is_file():
            text = pyproject.read_text(encoding="utf-8")
            m = re.search(r'(?m)^\s*version\s*=\s*"([^"]+)"', text)
            if m:
                return m.group(1).strip()
        version_file = install_dir / "VERSION"
        if version_file.is_file():
            return version_file.read_text(encoding="utf-8").strip() or None
    except OSError as exc:
        logger.warning("Could not read local version (%s).", exc)
    return None
