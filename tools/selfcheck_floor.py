"""Floors of the self-check: why a pass does not support a clean verdict."""

from __future__ import annotations

from typing import Mapping

from tools.selfcheck_consts import (
    FLOOR_CHARS_PER_SURFACE,
    FLOOR_FLAG_TOKENS,
    FLOOR_TOOL_MENTION_DIVISOR,
    FLOOR_TOOL_MENTIONS_MIN,
)
from tools.selfcheck_types import Collected


def unaccounted(collected: Collected) -> list:
    """Registered tools that were neither read nor recorded as a skip.

    The completeness equality, and the one bound here that cannot be tuned. A
    handler that silently disappears from a pass is how a gate becomes inert,
    so a pass that cannot account for every registered tool is not clean --
    it is incomplete, and says so.
    """
    read = {
        name[len("tool:") :] for name in collected.surfaces if name.startswith("tool:")
    }
    skipped = {
        s.surface[len("tool:") :]
        for s in collected.skips
        if s.surface.startswith("tool:")
    }
    return sorted(set(collected.registered) - read - skipped)


def _floor_misses(scanned: Mapping, surfaces: int, registered_count: int) -> list:
    out: list = []
    chars = int(scanned.get("chars", 0))
    if chars < FLOOR_CHARS_PER_SURFACE * surfaces:
        out.append(f"chars={chars} < {FLOOR_CHARS_PER_SURFACE} x {surfaces} surfaces")
    mentions = int(scanned.get("tool_mentions", 0))
    want = max(
        FLOOR_TOOL_MENTIONS_MIN, int(registered_count) // FLOOR_TOOL_MENTION_DIVISOR
    )
    if mentions < want:
        out.append(f"tool_mentions={mentions} < {want}")
    flags = int(scanned.get("flag_tokens", 0))
    if flags < FLOOR_FLAG_TOKENS:
        out.append(f"flag_tokens={flags} < {FLOOR_FLAG_TOKENS}")
    return out


def floor_violations(scanned: Mapping, *, registered_count: int, missing=()) -> list:
    """Why this pass does not support a clean verdict, in report order."""
    out: list = []
    if missing:
        out.append(
            "registered tools neither read nor skipped: " + ", ".join(sorted(missing))
        )
    surfaces = int(scanned.get("surfaces", 0))
    if not surfaces:
        out.append("no text surface was read at all")
        return out
    return out + _floor_misses(scanned, surfaces, registered_count)
