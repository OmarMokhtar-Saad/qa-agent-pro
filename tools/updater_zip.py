"""Updater release zipball download and safe extraction (split from tools/updater.py)."""

from __future__ import annotations

import stat
import zipfile
from pathlib import Path
from typing import NamedTuple

from tools import http_port
from tools.updater_github import _auth_headers


# Re-apply the archive's EXECUTE bits after extraction.
#
# ``ZipFile.extractall`` does not restore the POSIX mode, even though
# ``_safe_extract_zip`` has already parsed ``external_attr`` for its symlink
# check and could simply keep it. Everything executable in a release therefore
# landed non-executable. ``apply_update`` had a suffix-scoped workaround for
# ``*.sh`` only, so ``connect.sh`` survived and nothing else did; the sibling
# path shipped a tester-facing "./mvnw is not executable" message for the same
# root cause (framework_writer.py), which is this class reaching a real user.
#
# Bounded on purpose: the execute bits the archive itself carried, OR-ed onto
# what is there. Never setuid/setgid/sticky, never a bit the archive did not
# carry, and an OSError on one member is skipped rather than failing an
# extraction that has already passed every containment check.
def _restore_exec_bits(zf: zipfile.ZipFile, dest: Path) -> None:
    """Re-apply the archive's execute bits after extraction."""
    for info in zf.infolist():
        if info.is_dir():
            continue
        exec_bits = (info.external_attr >> 16) & 0o111
        if not exec_bits:
            continue
        target = dest / info.filename
        if target.is_symlink() or not target.is_file():
            continue
        try:
            target.chmod(stat.S_IMODE(target.stat().st_mode) | exec_bits)
        except OSError:
            continue


# Extract a release zip, refusing any member that would escape ``dest``.
#
# Zip-slip defense-in-depth: absolute paths, any ``..`` component, a
# target that resolves outside ``dest``, or a symlink member each abort
# the whole extraction with ``ValueError`` (the caller degrades to the
# 'error' status and starts the current version). Never extracts a
# single member before the whole archive has been validated.
#
# POSIX mode lives in the high 16 bits of external_attr for zips created on Unix
# (create_system==3); GitHub source zipballs qualify. On non-POSIX zips these
# bits are 0, so the path/traversal checks remain the primary guard.
def _safe_extract_zip(zf: zipfile.ZipFile, dest: Path) -> None:
    """Extract a release zip, refusing any member that would escape ``dest``."""
    dest_root = dest.resolve()
    for info in zf.infolist():
        name = info.filename
        if name.startswith(("/", "\\")) or ".." in Path(name).parts:
            raise ValueError(f"Unsafe zip member path: {name!r}")
        target = (dest / name).resolve()
        if target != dest_root and dest_root not in target.parents:
            raise ValueError(f"Zip member escapes extract dir: {name!r}")
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise ValueError(f"Unsafe symlink in zip: {name!r}")
    zf.extractall(dest)
    _restore_exec_bits(zf, dest)


class ZipSource(NamedTuple):
    """Where to fetch a release zipball and how (a parameter object)."""

    url: str
    token: str
    timeout: float


def download_and_extract(src: ZipSource, workdir: Path) -> Path:
    """Download the release zipball and extract it under ``workdir``. Returns the
    single top-level directory GitHub wraps the source in (``owner-repo-sha/``)."""
    resp = http_port.get(src.url, _auth_headers(src.token), src.timeout)
    resp.raise_for_status()
    zip_path = workdir / "release.zip"
    zip_path.write_bytes(resp.content)
    extract_dir = workdir / "extracted"
    with zipfile.ZipFile(zip_path) as zf:
        _safe_extract_zip(zf, extract_dir)
    subdirs = [p for p in extract_dir.iterdir() if p.is_dir()]
    return subdirs[0] if len(subdirs) == 1 else extract_dir
