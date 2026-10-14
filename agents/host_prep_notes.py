"""Submit-time disclosure notes: no coverage critique ran; cases dropped on parse."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise

logger = logging.getLogger("agents.host_mode")


def _no_coverage_text() -> str:
    return (
        "> ℹ️  **No automated coverage critique ran on this "
        "suite.** The server-side critic is a model call this chat-only path "
        "does not make, and no requirement matching runs on this server "
        "either -- so the generation-volume floor is the ONLY "
        "quantitative gate this suite passed. Read the cases against the "
        "requirements yourself before signing them off.\n\n"
    )


def no_coverage_signal_note(view: object) -> str:
    """Disclosure for a finalize that computed NO coverage signal at all.

    ALWAYS-ON on the submit path (no coverage view is built there); silent only
    when a caller passes a non-None ``view``. A disclosure that fired only on
    small suites would read, by its absence, as "coverage WAS checked". This
    does not run the critic; it says that none ran. Never raises.
    """
    # pragma: no cover - a disclosure must never raise
    with never_raise(logger, "_no_coverage_signal_note failed", exc_info=True):
        return "" if view is not None else _no_coverage_text()
    return ""


def _dropped_lines(parsed: object, n: int) -> list[str]:
    """The "dropped as malformed" block (reasons are capped upstream)."""
    reasons = getattr(parsed, "dropped_reasons", None) or []
    lines = [
        f"> ⚠️  {n} submitted case(s) were dropped as malformed and are not included:"
    ]
    return lines + [f">   - {r}" for r in reasons]


def _salvaged_lines(salvaged: list) -> list[str]:
    """Cases KEPT with only their optional test_data plan rejected."""
    lines = [
        f"> ℹ️  {len(salvaged)} case(s) were KEPT with their optional "
        "`test_data` plan removed, because that plan failed validation. "
        "The steps and expected results are unchanged:"
    ]
    return lines + [f">   - {r}" for r in salvaged]


def dropped_note(parsed) -> str:
    """Non-silent disclosure of cases dropped as malformed during parse/salvage.

    Surfaced UNCONDITIONALLY on BOTH the gap reply and the finished reply.
    Salvaged cases are in the suite, so they are not reported as dropped.
    """
    n = getattr(parsed, "dropped_count", 0) or 0
    salvaged = getattr(parsed, "salvaged_reasons", None) or []
    if not n and not salvaged:
        return ""
    lines = _dropped_lines(parsed, n) if n else []
    if salvaged:
        lines += [">"] if lines else []
        lines += _salvaged_lines(salvaged)
    return "\n".join(lines) + "\n\n"
