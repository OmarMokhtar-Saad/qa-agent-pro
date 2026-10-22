"""Updater release zipball download and safe extraction (split from tools/updater.py)."""

from __future__ import annotations

import stat
import zipfile
from pathlib import Path
from typing import NamedTuple

import httpx

from tools import http_port
from tools.updater_github import _auth_headers

#: Zip-bomb ceilings for a RELEASE zipball, owned here rather than borrowed from
#: the mitmdump archive caps: the updater ships in every dist, mobile or not, and
#: a real release carries ~2050 members (v1.153.0: 2051, 8 MB unpacked, largest
#: file 195 KB), twice the mitmdump 1000-member cap. See the CEILINGS rows in
#: ``tests/test_bounds_upper.py`` and the real-release floor in
#: ``tests/test_updater_zip_caps.py``.
MAX_ZIP_MEMBERS: int = 20_000
MAX_ZIP_MEMBER_BYTES: int = 64 * 1024 * 1024
MAX_ZIP_TOTAL_BYTES: int = 512 * 1024 * 1024


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
def _check_zip_size(zf: zipfile.ZipFile) -> None:
    """Refuse a zip with too many members or too many declared bytes (zip bomb)."""
    infos = zf.infolist()
    if len(infos) > MAX_ZIP_MEMBERS:
        raise ValueError(f"Release zip has too many members: {len(infos)}")
    if any(i.file_size > MAX_ZIP_MEMBER_BYTES for i in infos):
        raise ValueError("Release zip member exceeds the size cap")
    if sum(i.file_size for i in infos) > MAX_ZIP_TOTAL_BYTES:
        raise ValueError("Release zip exceeds the total size cap")


def _safe_extract_zip(zf: zipfile.ZipFile, dest: Path) -> None:
    """Extract a release zip, refusing any member that would escape ``dest``."""
    _check_zip_size(zf)
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


#: Ceiling on the release zipball streamed to disk (compressed bytes). See the
#: CEILINGS row in ``tests/test_bounds_upper.py`` for the constraint that sizes it.
MAX_DOWNLOAD_BYTES: int = 200 * 1024 * 1024
_CHUNK_BYTES = 64 * 1024


def _refuse_declared(resp: httpx.Response) -> None:
    """Refuse a body whose declared Content-Length is already over the cap."""
    declared = resp.headers.get("content-length") or ""
    if declared.isdigit() and int(declared) > MAX_DOWNLOAD_BYTES:
        raise ValueError("Release zip download exceeds the size cap")


def _stream_capped(resp: httpx.Response, zip_path: Path) -> None:
    """Write the body to ``zip_path`` in chunks; delete it and raise past the cap."""
    total = 0
    try:
        with open(zip_path, "wb") as handle:
            for chunk in resp.iter_bytes(_CHUNK_BYTES):
                total += len(chunk)
                if total > MAX_DOWNLOAD_BYTES:
                    raise ValueError("Release zip download exceeds the size cap")
                handle.write(chunk)
    except BaseException:
        zip_path.unlink(missing_ok=True)
        raise


def _download_zip(src: ZipSource, zip_path: Path) -> None:
    """Stream the release zipball to ``zip_path``, capped at MAX_DOWNLOAD_BYTES."""
    with http_port.stream_get(src.url, _auth_headers(src.token), src.timeout) as resp:
        resp.raise_for_status()
        _refuse_declared(resp)
        _stream_capped(resp, zip_path)


def download_and_extract(src: ZipSource, workdir: Path) -> Path:
    """Download the release zipball and extract it under ``workdir``. Returns the
    single top-level directory GitHub wraps the source in (``owner-repo-sha/``)."""
    zip_path = workdir / "release.zip"
    _download_zip(src, zip_path)
    extract_dir = workdir / "extracted"
    with zipfile.ZipFile(zip_path) as zf:
        _safe_extract_zip(zf, extract_dir)
    subdirs = [p for p in extract_dir.iterdir() if p.is_dir()]
    return subdirs[0] if len(subdirs) == 1 else extract_dir
