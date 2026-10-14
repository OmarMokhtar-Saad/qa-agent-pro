"""Machine-readable rows about THIS install, for a non-chat client.

The desktop app (``desktop/``) is an MCP client with no Python import path into
this tree, so every fact it shows a tester has to arrive through a tool reply.
``qa-doctor`` already establishes those facts -- but as PROSE, for a model to
read. Parsing that prose would make the desktop app a second, silent consumer of
a format nobody promised it.

So this module serves the SAME facts as rows, and the rule that keeps the two
surfaces from drifting is that it calls the same PRODUCERS the doctor calls and
re-derives nothing. ``PRODUCERS`` below names them, one entry per component, and
``tests/test_machine_report_tool_registered.py`` pins that every one of them is
still a symbol the doctor's own module reaches. That pin is STATIC on purpose:
calling ``handle_setup_check`` to compare surfaces would run a repairing,
network-touching handler inside a unit test.

**This module never writes.** Not a `.env`, not an MCP config, not a device.
``qa-doctor`` repairs; this reports. That split is why a desktop app can poll it
on a timer without a tester wondering what a refresh just changed.

Imports of the producers are LAZY, inside each builder, for the reason
``tools/flag_registry.py`` states about itself: importing this module must cost
nothing. None of those producers imports the composition root
(``handlers/``), and this module does not import it either: the
edition facts only the composition root knows arrive INJECTED, as the
``edition_probe`` callable ``handle_machine_report`` hands to ``collect``.
"""

from __future__ import annotations

import asyncio  # noqa: F401
import logging  # noqa: F401
import shutil  # noqa: F401
from dataclasses import asdict, dataclass  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import TYPE_CHECKING, Any

from tools.machine_report_clients import client_items
from tools.machine_report_core import (  # noqa: F401
    MAX_ROWS,
    STATUSES,
    Row,
    _rows,
    backend_info,
    install_dir,  # by value: patch machine_report_core.install_dir, its readers live there
    local_version,
    logger,
)
from tools.machine_report_host import privileges_row, version_row
from tools.machine_report_image import (  # noqa: F401
    SYSTEM_IMAGE_PROBE_TIMEOUT_S,
    image_items,
)
from tools.machine_report_sdk import (  # noqa: F401
    SDK_TOOL_ROWS,
    ime_row,
    sdk_items,
)

if TYPE_CHECKING:
    from collections.abc import Callable

#: component -> ``(module path, attribute)`` of the producer whose answer the
#: row carries. The doctor reads the SAME symbols; the coupling pin walks this
#: table, so a row can never outlive the producer it claims to report.
PRODUCERS = {
    "backend_version": ("tools.updater", "_local_version"),
    "host_privileges": ("tools.host_privileges", "probe"),
    "mobile_sdk": ("tools.mobile.sdk_locator", "locate_sdk"),
    # Same producer as `mobile_sdk`, different QUESTION: the SDK root is what
    # the emulator lives in, the adb binary is what a mirror and the mobile
    # lane must BOTH drive. A desktop client that guesses `adb` off PATH can
    # end up on a second adb server, where a device is visible in one place
    # and absent in the other.
    "adb": ("tools.mobile.sdk_locator", "locate_sdk"),
    # WHETHER THIS INSTALL CAN TYPE AT ALL. Without this row every component
    # could be green on a build with no keyboard pinned, where no script can
    # enter a character -- a machine that looks ready and is not.
    "mobile_ime": ("tools.mobile.ime", "manifest_status"),
    # The REST of the SDK's tools, one row each, same producer, different
    # question again. Without these a client has to read "Missing: emulator,
    # sdkmanager" out of the `mobile_sdk` row's prose to learn which tools are
    # absent -- which is the parse this module was written to make unnecessary.
    "sdk_tool:emulator": ("tools.mobile.sdk_locator", "locate_sdk"),
    "sdk_tool:sdkmanager": ("tools.mobile.sdk_locator", "locate_sdk"),
    "sdk_tool:avdmanager": ("tools.mobile.sdk_locator", "locate_sdk"),
    # The booted emulator's image, in `system-images;...` form: the tag the
    # desktop's HTTPS-decrypt opt-in reads (only `google_apis` can be rooted).
    "mobile_system_image": ("tools.mobile.sdk_locator", "avd_system_image"),
}

_SECTIONS = ("doctor", "clients", "provisioning", "backend")


def doctor_rows() -> list:
    """The machine-readiness rows, from the doctor's own producers."""
    rows: list[Any] = [version_row(), privileges_row()]
    rows.extend(sdk_items())
    rows.append(ime_row())
    rows.extend(image_items())
    return _rows(rows)


def system_image_rows() -> list:
    """The system-image rows alone, serialised -- what the pins read."""
    return _rows(image_items())


# Which MCP clients are installed, and whether THIS install is registered: the
# rows come from ``machine_report_clients.client_items`` (read-only by
# construction; see the comment above it).
def client_rows(home=None) -> list:
    return _rows(client_items(home))


_PROVISIONING_DETAIL = (
    "Auto-provisioning is retired: this server downloads no Android "
    "SDK, and creates no emulator unasked. When none is found, "
    "`qa_mobile_test` answers with a setup guide (install Android "
    "Studio and a system image; then create an AVD in its Device "
    "Manager or ask with `qa_mobile_test(emulator=create)`)."
)


# One fixed row: this server provisions no Android SDK or emulator.
#
# The ``provisioning`` SECTION is kept because a polling client (the
# ``desktop/`` app) may ask for it by name, and an unknown-section error would
# read as a broken server. It reads nothing and starts nothing
# (docs/RETIRED_CAPABILITIES.md -> 6).
def provisioning_rows() -> list:
    return _rows([Row("provisioning", "off", _PROVISIONING_DETAIL)])


def collect(
    section: str = "all", *, edition_probe: Callable[[], dict] | None = None
) -> dict:
    """The whole report, or one section of it. Never raises.

    ``edition_probe`` is passed on to ``backend_info``."""
    want = (section or "all").strip().lower()
    if want not in _SECTIONS and want != "all":
        return {"error": "unknown section: " + want, "sections": list(_SECTIONS)}
    out: dict = {"error": None, "section": want}
    if want in ("all", "backend"):
        out["backend"] = backend_info(edition_probe)
    if want in ("all", "doctor"):
        out["doctor"] = doctor_rows()
    if want in ("all", "clients"):
        out["clients"] = client_rows()
    if want in ("all", "provisioning"):
        out["provisioning"] = provisioning_rows()
    return out
