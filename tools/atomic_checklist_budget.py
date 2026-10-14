"""Budget helpers for the checklist prompt block: cap, truncation note, wrapper."""

from __future__ import annotations

import logging

from tools import settings_port
from tools.atomic_checklist_core import (
    _DEFAULT_MAX_PROMPT_CHARS,
    _MIN_PROMPT_CHARS,
    ChecklistItem,
)
from tools.untrusted import wrap_untrusted

logger = logging.getLogger("tools.atomic_checklist")


def _too_small(cap: int) -> tuple[str, list[str]]:
    logger.warning(
        "QA_CHECKLIST_MAX_PROMPT_CHARS (%d) is too small to present even "
        "one checklist item — the checklist block is omitted and every "
        "item is reported as NOT PRESENTED",
        cap,
    )
    return "", []


def _wrapped(body: str, cap: int, note: str) -> str:
    return (
        "## Atomic Requirements Checklist (every item must end up covered)\n"
        + wrap_untrusted("atomic_checklist", body, limit=cap)
        + note
    )


def _format_checklist_prompt_block_cap(limit: int | None) -> int:
    """The character budget for the block, never below the minimum."""
    cap = int(
        limit
        if limit is not None
        else (
            getattr(
                settings_port.get(),
                "qa_checklist_max_prompt_chars",
                _DEFAULT_MAX_PROMPT_CHARS,
            )
            or _DEFAULT_MAX_PROMPT_CHARS
        )
    )
    cap = max(_MIN_PROMPT_CHARS, cap)
    return cap


def _warn_truncated(missing: int, total: int, cap: int) -> None:
    logger.warning(
        "Atomic checklist prompt block truncated: %d of %d item(s) did not "
        "fit in QA_CHECKLIST_MAX_PROMPT_CHARS=%d and are reported as "
        "NOT PRESENTED.",
        missing,
        total,
        cap,
    )


def _truncation_note(missing: int) -> str:
    return (
        f"\n\n[{missing} further checklist item(s) did not fit in this "
        "prompt and are NOT shown above. They are tracked separately as "
        "NOT PRESENTED — do not try to guess them.]"
    )


def _format_checklist_prompt_block_note(
    items: list[ChecklistItem], presented: list[str], cap: int
) -> str:
    """The truncation note, or "" when every item was presented."""
    missing = len(items) - len(presented)
    if missing > 0:
        _warn_truncated(missing, len(items), cap)
        return _truncation_note(missing)
    return ""
