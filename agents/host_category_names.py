"""Host mode: canonical category names and normalisation of untrusted category text."""

from __future__ import annotations

import agents.test_scenario_agent as tsa
from agents.host_category_consts import _CATEGORY_ALIASES, logger
from config.settings_guard import never_raise


def _canonical_categories() -> list:
    """The 8 canonical category names. Empty on any failure -- callers degrade
    to the alias table alone."""
    try:
        return [str(c[0]) for c in tsa.CATEGORIES]
    except (TypeError, IndexError):
        logger.debug("could not read CATEGORIES", exc_info=True)
        return []


def _exact(folded: str, canon: list) -> str:
    """The canonical name whose casefold equals ``folded``, or ""."""
    for name in canon:
        if folded == name.casefold():
            return name
    return ""


def _alias(folded: str, canon: list) -> str:
    """The canonical name ``folded`` (or its part before any "/") aliases, or ""."""
    # `Positive / Happy Path` -> `positive`; `UI/UX Validation` -> `ui`.
    head = folded.split("/", 1)[0].strip()
    for key in (folded, head):
        hit = _CATEGORY_ALIASES.get(key)
        if hit:
            # Only return a name the canonical list still contains, so a
            # renamed category cannot resurrect a stale label.
            return hit if (not canon or hit in canon) else ""
    return ""


def _resolve(text: str, canon: list) -> str:
    """The exact casefolded match first, then the alias table; "" when neither hits."""
    folded = text.casefold()
    return _exact(folded, canon) or _alias(folded, canon)


# Returns "" when it cannot be resolved -- never a guess, and never the raw
# value, so nothing unvalidated reaches the exported artifact. Never raises.
#
# A strict match would blank nearly half the real traffic: of 27 observed
# per-category submissions, 13 (48%) used a non-canonical spelling -- 7 of the
# 15 distinct spellings seen. See tests/test_host_mode_submit.py for the
# fixture that recomputes this.
def normalize_category(raw: object) -> str:
    """Resolve UNTRUSTED category text onto one of the 8 canonical names."""
    with never_raise(logger, "category normalisation failed", exc_info=True):
        text = str(raw or "").strip()
        if not text:
            return ""
        return _resolve(text, _canonical_categories())
    return ""


def _find_category_row(prepared, canon: str) -> tuple | None:
    """The (name, focus, preferred_type) row of prepared.categories for canon."""
    for name, focus, ptype in getattr(prepared, "categories", None) or []:
        if name == canon or normalize_category(name) == canon:
            return (name, focus, ptype)
    return None
