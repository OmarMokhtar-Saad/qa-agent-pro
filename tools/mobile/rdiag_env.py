"""Diagnostics Environment & provenance section: system image, device, host and render time."""

from __future__ import annotations

import time

from tools.mobile import platform_info
from tools.mobile.rdiag_device import _device_sub
from tools.mobile.report_text import _short_stamp, esc

#: The sentence an OLD manifest gets. A run created before this field existed genuinely
#: does not know its image, and the page says exactly that. It NEVER falls back to
#: ``settings`` or to a live ``sdk_locator.avd_system_image()`` read: both answer with
#: TODAY's image, which is a fabricated fact about a finished run -- the
#: success-shaped absence this repository has paid for before.
SYSTEM_IMAGE_ABSENT = (
    "not recorded for this run: it was created before the image was snapshotted into "
    "the manifest, and today's setting is not evidence of what ran then"
)


def _image_row(image: str) -> str:
    return _env_row(
        "System image",
        esc(image, 120)
        if image
        else '<span class="cap none">' + esc(SYSTEM_IMAGE_ABSENT, 300) + "</span>",
        "snapshotted at run creation from sdk_locator.avd_system_image(); HTTPS body "
        "decryption is only possible on a rootable image"
        if image
        else "",
    )


def _env_section(manifest: dict, loaded: dict | None = None) -> str:
    """The Environment & provenance section body.

    Reads the manifest and ``platform_info.SUPPORT``; DERIVES nothing that a producer
    already owns. The system image comes from the manifest SNAPSHOT and from nowhere else.
    """
    book = manifest if isinstance(manifest, dict) else {}
    image = str(book.get("system_image") or "")
    rows = [
        _image_row(image),
        _env_row("Device", esc(_device_sub(book), 120)),
        _env_host_row(),
        _env_row(
            "Rendered",
            esc(_short_stamp(time.time()), 40),
            "when this page was BUILT, which is not when the run happened",
        ),
    ]
    return '<div class="runstrip">' + "".join(rows) + "</div>"


def _env_row(label: str, value: str, sub: str = "") -> str:
    return (
        '<div class="envrow"><span class="rfl">'
        + esc(label, 60)
        + '</span><span class="rfv">'
        + value
        + ("<small>" + esc(sub, 300) + "</small>" if sub else "")
        + "</span></div>"
    )


def _env_host_row() -> str:
    # platform_info has NO `host_os`, and `raw_platform()` returns sys.platform
    # ("darwin"), which is NOT a SUPPORT key -- its own docstring says it is for
    # REPORTING the host, never for branching. So the host is NAMED from
    # raw_platform() and the support claim is DERIVED by support_statement(),
    # which is the module's single producer of that sentence: promoting Windows
    # without a real run changes this row and the three docs together, or the
    # existing platform-support pin names each one.
    host = (
        platform_info.MACOS
        if platform_info.is_macos()
        else (platform_info.WINDOWS if platform_info.is_windows() else "")
    )
    return _env_row(
        "Host platform",
        esc(platform_info.raw_platform() or "(unknown)", 60),
        platform_info.support_statement(host),
    )
