"""The pinned mitmdump releases: locations, versions and per-platform pins.

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
"""

from __future__ import annotations

import logging
import platform
from pathlib import Path

from tools.mobile import platform_info
from tools.mobile_capture import paths

logger = logging.getLogger("tools.mobile_capture.mitm_provision")

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
        "sha256": ("0a09ee3b82569e8985aff8186e4792618b8e5d0c766098db093d09a87d4b013a"),
    },
    ("darwin", "x86_64"): {
        "url": (
            "https://downloads.mitmproxy.org/"
            + MITM_VERSION
            + "/mitmproxy-"
            + MITM_VERSION
            + "-macos-x86_64.tar.gz"
        ),
        "sha256": ("7998187f5a0d399ab796af4523d3ad830ebe690726a41bc3e1df47a8e477a641"),
    },
    ("linux", "x86_64"): {
        "url": (
            "https://downloads.mitmproxy.org/"
            + MITM_VERSION
            + "/mitmproxy-"
            + MITM_VERSION
            + "-linux-x86_64.tar.gz"
        ),
        "sha256": ("2e95286b618fa6fd33e5e62a78c2e5112571d85f42ec2bac29b97ee242bdb5c5"),
    },
    ("linux", "aarch64"): {
        "url": (
            "https://downloads.mitmproxy.org/"
            + MITM_VERSION
            + "/mitmproxy-"
            + MITM_VERSION
            + "-linux-aarch64.tar.gz"
        ),
        "sha256": ("b358643a6c4f4b39e33d985350f660b724fece95687d7daa899ef0c4e211f681"),
    },
}


def _leaf() -> str:
    return platform_info.exe(MITM_BIN)


def mitm_dir() -> Path:
    """Where a provisioned ``mitmdump`` release lives. Creates nothing."""
    return paths.capture_root() / MITM_DIR


def _version_dir(version: str) -> Path:
    """Where one pinned release is extracted to, whole. Creates nothing."""
    return mitm_dir() / version


def platform_key() -> tuple[str, str]:
    """``(system, machine)``, lower-cased, straight from the stdlib.

    No arch-name translation sits in front of this: the vendor's own asset
    names already match ``platform.machine()``'s raw spelling on every
    platform :data:`MITM_PINS` carries (``arm64``/``x86_64`` on macOS,
    ``x86_64``/``aarch64`` on Linux). Adding a translation layer would need
    its own per-branch test for no benefit today.
    """
    return (platform.system().lower(), platform.machine().lower())


def pin_for_platform() -> dict[str, str] | None:
    """The pin for THIS host, or ``None`` if the platform is unlisted."""
    return MITM_PINS.get(platform_key())
