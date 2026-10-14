"""Which files a tester may import cases from."""

from __future__ import annotations

import pathlib
import tempfile
from pathlib import Path

from tools.process_env import home_dir

_SOURCE_SUFFIXES = (".csv", ".xlsx", ".md", ".markdown", ".txt")


def _source_roots() -> list[Path]:
    """Where a tester's own case file may live: their home and the temp dir."""
    roots = [Path(tempfile.gettempdir())]
    try:
        roots.append(home_dir())
    except (RuntimeError, KeyError):  # pragma: no cover - no home directory
        pass
    resolved = []
    for root in roots:
        try:
            resolved.append(root.resolve())
        except OSError:  # pragma: no cover - defensive
            continue
    return resolved


def _check_suffix(path: Path) -> None:
    if path.suffix.lower() not in _SOURCE_SUFFIXES:
        raise PermissionError(
            "Cannot import "
            + path.name
            + ": give a .csv, .xlsx, .md or .txt path, or paste a markdown table"
        )


def _allowed(resolved: Path) -> bool:
    for root in _source_roots():
        if resolved.is_relative_to(root):
            return not any(
                part.startswith(".") for part in resolved.relative_to(root).parts
            )
    return False


# ``cases`` reaches here from an MCP argument, so a path is untrusted: it must
# be a plain (non-symlink) .csv/.xlsx/.md/.txt file under the user's home or
# the temp dir, and not inside a dot-directory (``~/.ssh``, ``~/.aws``).
def vet_source_path(path: Path) -> None:
    """Raise ``PermissionError`` unless *path* is a case file we may read."""
    _check_suffix(path)
    if path.is_symlink():
        raise PermissionError("a symbolic link cannot be imported")
    if _allowed(path.resolve()):
        return
    raise PermissionError(
        "that file is outside the folders cases can be imported from "
        "(your home folder or the temp folder)"
    )


def _path_shaped(text: str) -> bool:
    if not text or "\n" in text or "\r" in text or "\x00" in text:
        return False
    return len(text) <= 4096


def _existing_file(candidate: str) -> "pathlib.Path | None":
    try:
        resolved = pathlib.Path(candidate).expanduser()
        return resolved if resolved.is_file() else None
    except (OSError, ValueError):
        return None


# ``Path(text).is_file()`` raises ``OSError`` for input that is not
# path-shaped -- ENAMETOOLONG on a long component, ENOTDIR, embedded NUL --
# and a ``len(text) < 4096`` guard does NOT prevent it, because the limit is
# per COMPONENT (255 bytes on macOS), not per path. A 469-character pasted
# table raised ENAMETOOLONG, the outer handler reported "Could not read that
# source", and ``load()`` therefore refused EVERY markdown paste while
# ``from_markdown`` handled the same text fine. A probe for "is this a file?"
# must answer False, not detonate.
def _maybe_path(text: str) -> "pathlib.Path | None":
    """*text* as an existing file, or None. NEVER raises."""
    candidate = str(text or "")
    if not _path_shaped(candidate):
        return None
    resolved = _existing_file(candidate)
    if resolved is None:
        return None
    vet_source_path(resolved)
    return resolved
