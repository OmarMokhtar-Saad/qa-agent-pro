"""Bounds the advisory block to its byte budget: examples shrink first, findings last."""

from __future__ import annotations

import logging

from tools import suite_consistency_limits as _lim
from tools.suite_consistency_trim import _drop_bullets_to_fit, _shrink_rows_to_fit

logger = logging.getLogger("tools.suite_consistency")


def _split_groups(lines: list[str]) -> tuple[list[str], list[list[str]]]:
    """Split *lines* into (header, bullets) on the top-level ``- **`` markers."""
    groups: list[list[str]] = [[]]
    for line in lines:
        if line.startswith("- **"):
            groups.append([])
        groups[-1].append(line)
    return groups[0], groups[1:]


# SPEND EXAMPLES BEFORE FINDINGS. Shrink every bullet's row list toward
# _MIN_ROWS_SHOWN, checking fit after each step, and only fall through to
# dropping whole bullets if even the floor does not fit. Uniform across
# bullets rather than largest-first: the largest bullet is not the least
# important one, and per-bullet favouritism is the framing F14 faulted.


def _fit_or_drop(
    groups: tuple[list[str], list[list[str]]],
    order: list[tuple[str, int]],
    joined: str,
) -> str:
    header, bullets = groups
    fitted, bullets = _shrink_rows_to_fit(header, bullets, joined)
    if fitted is not None:
        return fitted
    out = _drop_bullets_to_fit(header, bullets, order)
    if out is None:
        return joined
    # Dropping may never make the block LONGER than leaving it alone -- the
    # same net-loss rule mcp_handlers.assemble_finalize_reply enforces.
    return out if len(out) < len(joined) else joined


# Contract of _bound_advisory_block, kept whole here because the function itself
# is held to 20 lines (its docstring says only the first sentence):
#
# *order* is (name, priority) for each bullet actually rendered, in the order
# it was appended -- priority ``0`` means protected. Bullets are split off the
# assembled lines by their ``- **`` markers rather than being buffered
# separately, so every bullet body above is untouched.
#
# UNDER budget the result is byte-identical to ``"\\n".join(lines)``: that fast
# path is the point, so every assertion pinned on today's block stays true.
# OVER budget the order of sacrifice is EXAMPLES, then findings: every bullet's
# row list shrinks toward ``_MIN_ROWS_SHOWN`` first, keeping every headline;
# only if the floor still does not fit do whole bullets drop, in strict
# priority order and never truncated mid-bullet, because half a finding with a
# tc_id and no verdict is worse than a named absence. Protected bullets never
# drop. Never raises.
def _bound_advisory_block(lines: list[str], order: list[tuple[str, int]]) -> str:
    """Join *lines* into the advisory block, bounded by ``_BLOCK_CAP``. Never raises."""
    try:
        joined = "\n".join(lines)
        if len(joined) <= _lim._BLOCK_CAP:
            return joined
        header, bullets = _split_groups(lines)
        if len(bullets) != len(order):  # pragma: no cover - defensive
            return joined
        return _fit_or_drop((header, bullets), order, joined)
    except Exception:
        logger.exception("advisory block budget failed - returning it unbounded")
        return "\n".join(lines)
