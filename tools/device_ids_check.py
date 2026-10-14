"""Device-id and package-name whitelists (stdlib-only leaf of ``tools.device_ids``)."""

from __future__ import annotations

import re

# Whitelist a device identifier before it is ever passed to a subprocess.
# adb serials use alphanumerics plus . _ : - ; iOS UDIDs are hex + hyphen (or a
# 25-char hex form). This rejects spaces, quotes, slashes, and every shell
# metacharacter outright, so no crafted "id" can smuggle extra arguments. The
# FIRST character may NOT be a hyphen, so an id can never be mistaken for a
# command-line option/flag by the subprocess (defence in depth on top of the
# arg-list, no-shell exec).
_DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9._:][A-Za-z0-9._:\-]{0,127}$")


def _valid_device_id(device_id: object) -> bool:
    """True when *device_id* is a non-empty string matching the safe whitelist."""
    return bool(isinstance(device_id, str) and _DEVICE_ID_RE.match(device_id))


# A package / bundle identifier is reverse-DNS: dot-separated segments of
# letters, digits, and underscores, with at least one dot. This whitelist
# rejects EVERY shell metacharacter, whitespace, quote, slash, and a leading
# hyphen outright, so a device-reported id can never smuggle extra arguments
# into a later subprocess call (defence in depth on top of the arg-list,
# no-shell exec). Length is capped well above any real-world identifier.
_PACKAGE_NAME_RE = re.compile(r"^[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+$")

_MAX_PACKAGE_LEN = 205

# `pm list packages` (without -3) also returns framework packages; drop the
# obvious system namespaces when we fall back to the full list on emulators
# whose third-party list comes back empty.
_ANDROID_SYSTEM_PREFIXES = ("com.android.", "com.google.android.", "android.")

#: Public alias of :data:`_ANDROID_SYSTEM_PREFIXES`, for callers outside this
#: module (e.g. ``tools.mobile.adb.clear_app_data`` refusing to wipe a system
#: namespace) that should not import the underscore-private name -- same
#: reason :func:`valid_package_name` wraps :func:`_valid_package_name`.
SYSTEM_PACKAGE_PREFIXES = _ANDROID_SYSTEM_PREFIXES


def _valid_package_name(name: object) -> bool:
    """True when *name* is a safe, whitelist-matching package / bundle id."""
    return bool(
        isinstance(name, str)
        and 0 < len(name) <= _MAX_PACKAGE_LEN
        and _PACKAGE_NAME_RE.match(name)
    )


def valid_package_name(name: object) -> bool:
    """Public wrapper over :func:`_valid_package_name`.

    Callers outside this module (e.g. mcp_handlers re-validating a
    client-controllable app id before reusing it in a subprocess) should use
    this public name rather than importing the underscore-private helper."""
    return _valid_package_name(name)


def parse_pm_packages(text: str, *, drop_system: bool = False) -> list[str]:
    """Parse ``pm list packages`` text ('package:<id>' per line) into ids.

    Every id is whitelist-validated before inclusion; invalid ids (and, when
    *drop_system*, obvious system namespaces) are skipped. Duplicates collapse;
    ids come back in first-seen order."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("package:"):
            continue
        pkg = line[len("package:") :].strip()
        if drop_system and pkg.startswith(_ANDROID_SYSTEM_PREFIXES):
            continue
        if pkg in seen or not _valid_package_name(pkg):
            continue
        seen.add(pkg)
        out.append(pkg)
    return out
