"""Android system package families (Play Store, browsers, Google services, vendor
system apps). Imports nothing, so ``target_pin`` and ``knowledge_scope`` share it."""

from __future__ import annotations

SYSTEM_PREFIXES: tuple[str, ...] = (
    "com.android.",
    "com.google.android.gms",
    "com.google.android.gsf",
    "com.google.android.googlequicksearchbox",
    "com.google.android.packageinstaller",
    "com.google.android.permissioncontroller",
    "com.samsung.android.",
    "com.sec.android.",
    "com.miui.",
    "org.mozilla.",
    "com.brave.",
    "com.opera.",
    "com.microsoft.emmx",
)


def is_system(package: object) -> bool:
    """True when *package* belongs to one of the system families."""
    return str(package or "").strip().startswith(SYSTEM_PREFIXES)
