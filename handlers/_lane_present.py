"""Leaf probe: did the mobile lane ship? Imports nothing from ``handlers``.

``scripts/build_dist.py`` ships ``handlers/mobile`` and ``tools/mobile`` only
together, and only when ``tools/mobile/ime_manifest.py`` exists, so that module
being importable is exactly "the lane is present". Only the absence of the
lane's own package reads as absent; any other missing module still raises.
"""

from __future__ import annotations

from typing import Any, cast

try:
    import tools.mobile.ime_manifest as ime_manifest
except ModuleNotFoundError as exc:  # pragma: no cover - dist-simulation covers it
    if exc.name not in ("tools.mobile", "tools.mobile.ime_manifest"):
        raise
    ime_manifest = cast("Any", None)


def mobile_present() -> bool:
    """True when the mobile lane's packages shipped."""
    return ime_manifest is not None
