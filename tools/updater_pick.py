"""Pick the server release among GitHub releases (an IME tag is never one)."""

from __future__ import annotations

import re
from typing import Optional

_SERVER_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def server_tag_key(tag: object) -> Optional[tuple]:
    """``(major, minor, patch)`` for a plain ``vX.Y.Z`` server tag, else None
    (``qa-ime-v1.2.2`` and every other prefixed or suffixed tag is not one)."""
    m = _SERVER_TAG.match(str(tag or "").strip())
    return tuple(int(g) for g in m.groups()) if m else None


def is_server_release(release: dict) -> bool:
    """True for a published (not draft, not pre-release) ``vX.Y.Z`` release."""
    if release.get("draft") or release.get("prerelease"):
        return False
    return server_tag_key(release.get("tag_name")) is not None


def highest_server_release(releases: list) -> Optional[dict]:
    """The published release with the highest ``vX.Y.Z`` tag, or None."""
    found = [r for r in releases if isinstance(r, dict) and is_server_release(r)]
    return max(found, key=lambda r: server_tag_key(r["tag_name"]) or (), default=None)
