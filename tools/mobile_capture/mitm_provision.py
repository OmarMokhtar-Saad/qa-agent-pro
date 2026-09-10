"""Provision `mitmdump` on demand, the SDK/emulator provisioner's own pattern.

**Locate before download.** :func:`find_mitmdump` checks PATH, then searches
``capture/mitm/`` for a previously-extracted binary -- mirroring
``tools/mobile/sdk_locator.py`` -- so a machine that already has mitmproxy
installed is never shadowed by a redundant fetch. Nothing downloads before
that check runs.

**The vendor archive is a whole bundle, not one file.** A real
``mitmdump`` binary is PyInstaller-frozen: on macOS it sits inside
``mitmproxy.app/Contents/MacOS/mitmdump`` and needs its sibling
``Python.framework`` and ``.so``/``.dylib`` files beside it to start at all
-- pulling the executable out by basename alone ships a binary that exits
255 with "Failed to load Python shared library". This module therefore
extracts the WHOLE archive into a per-version directory
(``capture/mitm/<version>/``) and then RESOLVES the binary's path inside it
by searching for its basename, rather than assuming one flat layout. Linux's
own release is a flat 3-member tarball with ``mitmdump`` at the archive
root -- the shipped single-member extraction happened to work there, which
is why a whole-archive design is still graded against BOTH real shapes (see
the test module).

**Per-platform pins, one table.** :data:`MITM_PINS` keys a
``{"url", "sha256"}`` pair by ``(platform.system().lower(),
platform.machine().lower())`` -- the vendor's own asset names already match
those raw values (``arm64``/``x86_64`` on macOS, ``x86_64``/``aarch64`` on
Linux), so no arch-name translation table is needed, and none is added: a
translation would need its own per-branch test, and the raw values already
line up. A host whose key is not in the table (Windows, 32-bit ARM Linux, an
unknown platform) refuses BY NAME (``REASON_NO_MITM_PIN``) rather than
attempting an unverifiable fetch -- the same state
``tools/mobile/provisioner.py`` already names ``BLOCKED_UNPINNED`` for the
JRE and cmdline-tools steps.

Reuses ``tools.mobile.downloader.download`` for the fetch: HTTPS-only,
hash-verified, every redirect hop checked against
``downloader.ALLOWED_HOST_SUFFIXES``. Nothing here opens a socket of its own.

Never raises: every public function returns ``{"error", "content"}``.
"""

from __future__ import annotations

import logging
import platform
import shutil
import tarfile
import zipfile
from pathlib import Path

from tools.mobile import downloader, platform_info
from tools.mobile_capture import paths

logger = logging.getLogger(__name__)

#: Sub-directory of the capture root a provisioned binary lives under.
MITM_DIR = "mitm"

#: The binary name this module locates and provisions. ``platform_info.exe``
#: appends ``.exe`` on Windows, matching ``sdk_locator``'s convention.
MITM_BIN = "mitmdump"

#: The canonical mitmproxy distribution domain. Added to
#: ``downloader.ALLOWED_HOST_SUFFIXES`` in this same change: mitmproxy also
#: publishes releases on GitHub (already allowlisted, for the QA IME asset),
#: but pinning to the project's own domain keeps this fetch independent of
#: that unrelated allowlist entry ever being narrowed for IME-only reasons.
MITM_HOST_SUFFIX = "mitmproxy.org"

#: The pinned mitmproxy release. One version for every platform in
#: :data:`MITM_PINS`; bumping it means re-measuring every entry below, never
#: editing one in isolation.
MITM_VERSION = "12.2.3"

#: Per-platform pins, keyed by ``(platform.system().lower(),
#: platform.machine().lower())`` -- see the module docstring for why no
#: arch-name translation sits in front of this table. Every value below was
#: measured by downloading the real vendor asset and hashing the bytes
#: (verbatim, never re-derived or guessed):
#:
#: * macOS arm64 and macOS x86_64 are 327-member PyInstaller ``.app``
#:   bundles; ``mitmdump`` sits at ``mitmproxy.app/Contents/MacOS/mitmdump``.
#: * Linux x86_64 and Linux aarch64 are flat 3-member tarballs with
#:   ``mitmdump`` at the archive root.
#:
#: Windows is absent: the vendor ships a Windows installer ``.exe`` rather
#: than a tarball this module can extract, and the mobile lane already
#: discloses Windows as unverified (``tools/mobile/platform_info.SUPPORT``).
#: 32-bit ARM Linux (``linux``, ``armv7l``/``armv8l``) has no vendor asset
#: either. Both shapes -- and any future platform -- fall through to
#: :data:`REASON_NO_MITM_PIN` because their key is simply absent here; adding
#: a platform or bumping the version is one entry, no code change.
MITM_PINS: dict[tuple[str, str], dict[str, str]] = {
    ("darwin", "arm64"): {
        "url": (
            "https://downloads.mitmproxy.org/"
            + MITM_VERSION
            + "/mitmproxy-"
            + MITM_VERSION
            + "-macos-arm64.tar.gz"
        ),
        "sha256": (
            "0a09ee3b82569e8985aff8186e4792618b8e5d0c766098db093d09a87d4b013a"
        ),
    },
    ("darwin", "x86_64"): {
        "url": (
            "https://downloads.mitmproxy.org/"
            + MITM_VERSION
            + "/mitmproxy-"
            + MITM_VERSION
            + "-macos-x86_64.tar.gz"
        ),
        "sha256": (
            "7998187f5a0d399ab796af4523d3ad830ebe690726a41bc3e1df47a8e477a641"
        ),
    },
    ("linux", "x86_64"): {
        "url": (
            "https://downloads.mitmproxy.org/"
            + MITM_VERSION
            + "/mitmproxy-"
            + MITM_VERSION
            + "-linux-x86_64.tar.gz"
        ),
        "sha256": (
            "2e95286b618fa6fd33e5e62a78c2e5112571d85f42ec2bac29b97ee242bdb5c5"
        ),
    },
    ("linux", "aarch64"): {
        "url": (
            "https://downloads.mitmproxy.org/"
            + MITM_VERSION
            + "/mitmproxy-"
            + MITM_VERSION
            + "-linux-aarch64.tar.gz"
        ),
        "sha256": (
            "b358643a6c4f4b39e33d985350f660b724fece95687d7daa899ef0c4e211f681"
        ),
    },
}

#: Ceiling on the mitmdump ARCHIVE this module downloads (the compressed
#: bytes) and, separately, on any SINGLE member inside it. See the CEILINGS
#: row in ``tests/test_bounds_upper.py`` for the constraint that sizes this.
MAX_ARCHIVE_BYTES: int = 200 * 1024 * 1024

#: Ceiling on the SUM of every extracted file's bytes across the whole
#: archive. A many-small-members archive can walk straight past
#: ``MAX_ARCHIVE_BYTES`` (which only bounds one member at a time) while
#: staying under it on every individual file -- this is what closes that
#: gap. See the CEILINGS row in ``tests/test_bounds_upper.py``.
MAX_ARCHIVE_TOTAL_BYTES: int = 300 * 1024 * 1024

#: Ceiling on the NUMBER of members an archive may declare. A real mitmdump
#: release carries either 3 (Linux, flat) or 327 (macOS, the ``.app``
#: bundle) members. See the CEILINGS row in ``tests/test_bounds_upper.py``.
MAX_ARCHIVE_MEMBERS: int = 1000

#: Refusal codes. Constants rather than prose, so a caller branches by NAME.
REASON_NO_MITMDUMP = "no_mitmdump"
REASON_NO_MITM_PIN = "no_mitm_pin"
REASON_NO_MITM_BINARY = "no_mitm_binary"


class _TraversalRefused(Exception):
    """An archive member would resolve outside the destination directory.

    Raised internally by :func:`_safe_join` and caught inside
    :func:`_extract_archive` -- it never escapes this module's public
    surface, which keeps the ``{"error", "content"}`` contract intact.
    """


def _leaf() -> str:
    return platform_info.exe(MITM_BIN)


def mitm_dir() -> Path:
    """Where a provisioned ``mitmdump`` release lives. Creates nothing."""
    return paths.capture_root() / MITM_DIR


def _version_dir(version: str) -> Path:
    """Where one pinned release is extracted to, whole. Creates nothing."""
    return mitm_dir() / version


def _platform_key() -> tuple[str, str]:
    """``(system, machine)``, lower-cased, straight from the stdlib.

    No arch-name translation sits in front of this: the vendor's own asset
    names already match ``platform.machine()``'s raw spelling on every
    platform :data:`MITM_PINS` carries (``arm64``/``x86_64`` on macOS,
    ``x86_64``/``aarch64`` on Linux). Adding a translation layer would need
    its own per-branch test for no benefit today.
    """
    return (platform.system().lower(), platform.machine().lower())


def _pin_for_platform() -> dict[str, str] | None:
    """The pin for THIS host, or ``None`` if the platform is unlisted."""
    return MITM_PINS.get(_platform_key())


def _resolve_binary(root: Path, leaf: str) -> Path | None:
    """Search the extracted tree for *leaf* by basename.

    The layout differs per platform -- macOS buries the executable in
    ``mitmproxy.app/Contents/MacOS/``, Linux ships it flat at the archive
    root -- so this makes neither shape special. The first match, in a
    deterministic (sorted) order, so a fixture with more than one candidate
    still resolves the same file on every run.
    """
    try:
        matches = sorted(p for p in root.rglob(leaf) if p.is_file())
    except OSError:
        return None
    return matches[0] if matches else None


def _candidates() -> list[tuple[str, Path]]:
    """``(source label, path)`` in priority order. PATH first, ours last --
    the same "an existing install wins" priority ``sdk_locator`` uses."""
    out: list[tuple[str, Path]] = []
    on_path = shutil.which(MITM_BIN)
    if on_path:
        out.append(("PATH", Path(on_path)))
    cached = _resolve_binary(mitm_dir(), _leaf())
    if cached is not None:
        out.append(("qa-agents-cache", cached))
    return out


def find_mitmdump() -> dict:
    """The best available ``mitmdump``, or ``found: False``. Never raises.

    Checked BEFORE :func:`provision` proposes any download -- the property
    ``test_an_existing_binary_on_path_is_used_and_nothing_is_downloaded``
    proves by spying on ``downloader.download`` rather than reading a log
    line.
    """
    try:
        for source, candidate in _candidates():
            try:
                if candidate.is_file():
                    return {
                        "error": None,
                        "content": {
                            "path": str(candidate),
                            "source": source,
                            "found": True,
                        },
                    }
            except OSError:
                continue
        return {
            "error": None,
            "content": {"path": "", "source": "", "found": False},
        }
    except Exception as exc:
        logger.exception("mobile_capture.mitm_provision.find_mitmdump failed")
        return {"error": str(exc), "content": None}


def _archive_suffix(url: str) -> str:
    name = str(url or "").rsplit("/", 1)[-1]
    if name.lower().endswith(".tar.gz"):
        return ".tar.gz"
    if name.lower().endswith(".tgz"):
        return ".tgz"
    if name.lower().endswith(".zip"):
        return ".zip"
    return ".bin"


def _safe_join(dest_dir: Path, member_name: str) -> Path:
    """Resolve *member_name* under *dest_dir*, refusing an escape.

    This is the FIRST of two independent traversal guards -- an explicit
    containment check run against every member before anything is written.
    The second is ``filter="data"`` on ``tarfile.extractall`` (PEP 706,
    Python 3.12+). Neither is trusted alone: the tests drive each with the
    other disabled.
    """
    dest_resolved = dest_dir.resolve()
    candidate = (dest_dir / member_name).resolve()
    try:
        candidate.relative_to(dest_resolved)
    except ValueError:
        raise _TraversalRefused(member_name) from None
    return candidate


def _extract_tar(archive_path: Path, dest_dir: Path) -> dict:
    with tarfile.open(archive_path, "r:gz") as tf:
        members = tf.getmembers()
        if len(members) > MAX_ARCHIVE_MEMBERS:
            return {
                "error": (
                    "archive has "
                    + str(len(members))
                    + " members, over MAX_ARCHIVE_MEMBERS -- refusing"
                ),
                "content": None,
            }
        total = 0
        for member in members:
            _safe_join(dest_dir, member.name)
            if member.isfile():
                if member.size > MAX_ARCHIVE_BYTES:
                    return {
                        "error": (
                            member.name
                            + " inside the archive is "
                            + str(member.size)
                            + " bytes, over MAX_ARCHIVE_BYTES -- refusing"
                        ),
                        "content": None,
                    }
                total += member.size
                if total > MAX_ARCHIVE_TOTAL_BYTES:
                    return {
                        "error": (
                            "the archive's extracted total exceeds "
                            "MAX_ARCHIVE_TOTAL_BYTES -- refusing"
                        ),
                        "content": None,
                    }
        # Second traversal guard, independent of `_safe_join` above -- see
        # its docstring.
        tf.extractall(dest_dir, filter="data")
    return {"error": None, "content": {"dest_dir": str(dest_dir)}}


def _extract_zip(archive_path: Path, dest_dir: Path) -> dict:
    with zipfile.ZipFile(archive_path) as zf:
        infos = zf.infolist()
        if len(infos) > MAX_ARCHIVE_MEMBERS:
            return {
                "error": (
                    "archive has "
                    + str(len(infos))
                    + " members, over MAX_ARCHIVE_MEMBERS -- refusing"
                ),
                "content": None,
            }
        targets: list[tuple[zipfile.ZipInfo, Path]] = []
        total = 0
        for info in infos:
            target = _safe_join(dest_dir, info.filename)
            if info.is_dir():
                targets.append((info, target))
                continue
            if info.file_size > MAX_ARCHIVE_BYTES:
                return {
                    "error": (
                        info.filename
                        + " inside the archive is "
                        + str(info.file_size)
                        + " bytes, over MAX_ARCHIVE_BYTES -- refusing"
                    ),
                    "content": None,
                }
            total += info.file_size
            if total > MAX_ARCHIVE_TOTAL_BYTES:
                return {
                    "error": (
                        "the archive's extracted total exceeds "
                        "MAX_ARCHIVE_TOTAL_BYTES -- refusing"
                    ),
                    "content": None,
                }
            targets.append((info, target))
        # Every member validated (size, count, containment) before any byte
        # is written -- a rejected archive leaves nothing behind.
        for info, target in targets:
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            data = zf.read(info)
            if len(data) > MAX_ARCHIVE_BYTES:
                return {
                    "error": (
                        info.filename
                        + " exceeds MAX_ARCHIVE_BYTES once read -- refusing"
                    ),
                    "content": None,
                }
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    return {"error": None, "content": {"dest_dir": str(dest_dir)}}


def _extract_archive(archive_path: Path, dest_dir: Path) -> dict:
    """Extract the WHOLE *archive_path* into *dest_dir* -- never a single
    member.

    A PyInstaller-bundled ``mitmdump`` needs its sibling
    ``Python.framework``/``.so`` files beside the executable to start at
    all; pulling one member out by basename ships a binary that exits 255
    with "Failed to load Python shared library". Bounded by
    ``MAX_ARCHIVE_MEMBERS`` (member count), ``MAX_ARCHIVE_BYTES`` (each
    member) and ``MAX_ARCHIVE_TOTAL_BYTES`` (the sum of every extracted
    file) -- an archive of many small members can no longer walk past the
    per-member cap the way single-member extraction used to allow.
    """
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        name_lower = archive_path.name.lower()
        if name_lower.endswith((".tar.gz", ".tgz")):
            return _extract_tar(archive_path, dest_dir)
        if name_lower.endswith(".zip"):
            return _extract_zip(archive_path, dest_dir)
        return {
            "error": "unrecognised archive format: " + archive_path.name,
            "content": None,
        }
    except _TraversalRefused as exc:
        return {
            "error": (
                "refusing a member path that escapes the destination "
                "directory: " + str(exc)
            ),
            "content": None,
        }
    except Exception as exc:
        logger.exception("mobile_capture.mitm_provision: extraction failed")
        return {"error": str(exc), "content": None}


def provision(apply: bool = False) -> dict:
    """Locate, or (with ``apply=True``) fetch, ``mitmdump``. Never raises.

    ``apply=False`` (the default) NEVER downloads: an already-found binary is
    reported, otherwise the reply refuses by name (``REASON_NO_MITMDUMP``)
    and names the exact next call, rather than a bare ``None``.

    ``apply=True`` reuses ``downloader.download`` with this host's pin from
    :data:`MITM_PINS`. A host whose platform key is absent from that table
    refuses by name (``REASON_NO_MITM_PIN``) instead of attempting an
    unverifiable fetch. The archive is extracted WHOLE into a per-version
    directory and the binary's absolute path is RESOLVED inside it -- never
    assumed -- and that resolved path is what is recorded and returned.
    """
    try:
        found = find_mitmdump()
        if found["error"]:
            return found
        if found["content"]["found"]:
            return {
                "error": None,
                "content": {**found["content"], "downloaded": False},
            }
        if not apply:
            return {
                "error": REASON_NO_MITMDUMP,
                "content": {
                    "found": False,
                    "path": "",
                    "next_call": (
                        "tools.mobile_capture.mitm_provision.provision(apply=True)"
                    ),
                },
            }
        pin = _pin_for_platform()
        if not pin or not pin.get("url") or not downloader.valid_sha256(
            pin.get("sha256")
        ):
            key = _platform_key()
            return {
                "error": REASON_NO_MITM_PIN,
                "content": {
                    "found": False,
                    "path": "",
                    "detail": (
                        "No qa-agents release pins mitmdump's SHA-256 for "
                        "this host (" + key[0] + "/" + key[1] + ") yet, so "
                        "it cannot be downloaded and verified. Install "
                        "mitmproxy yourself (https://mitmproxy.org/downloads/) "
                        "and put `mitmdump` on PATH, or wait for a pinned "
                        "release."
                    ),
                },
            }
        url = pin["url"]
        sha256 = pin["sha256"]
        dest_dir = _version_dir(MITM_VERSION)
        archive_dest = mitm_dir() / ("download" + _archive_suffix(url))
        got = downloader.download(
            url,
            archive_dest,
            sha256,
            payload_bytes=MAX_ARCHIVE_BYTES,
        )
        if got["error"]:
            return {"error": got["error"], "content": None}
        extracted = _extract_archive(Path(got["content"]["path"]), dest_dir)
        try:
            Path(got["content"]["path"]).unlink(missing_ok=True)
        except OSError:
            pass
        if extracted["error"]:
            return {"error": extracted["error"], "content": None}
        binary = _resolve_binary(Path(extracted["content"]["dest_dir"]), _leaf())
        if binary is None:
            return {
                "error": REASON_NO_MITM_BINARY,
                "content": {
                    "detail": (
                        "the archive extracted cleanly but no "
                        + _leaf()
                        + " was found anywhere under "
                        + extracted["content"]["dest_dir"]
                    ),
                },
            }
        try:
            binary.chmod(0o755)
        except OSError:
            logger.info(
                "mobile_capture.mitm_provision: could not set the executable "
                "bit on %s",
                binary,
            )
        return {
            "error": None,
            "content": {
                "path": str(binary),
                "source": "downloaded",
                "found": True,
                "downloaded": True,
            },
        }
    except Exception as exc:
        logger.exception("mobile_capture.mitm_provision.provision failed")
        return {"error": str(exc), "content": None}
