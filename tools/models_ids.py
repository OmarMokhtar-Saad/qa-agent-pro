from __future__ import annotations

import logging
import re

from config.settings_guard import never_raise

logger = logging.getLogger("tools.models")


# --------------------------------------------------------------------------- #
# Requirement-id helpers
#
# They live HERE, beside TestCase, rather than in tools/rtm.py where
# normalize_ac_id was born, because the five exporters need them on every row of
# every export and tools.rtm carries deterministic requirement_id traceability
# a plain CSV / feature file / Playwright skeleton has no reason to load. The
# split stays because tools.rtm RE-EXPORTS normalize_ac_id, so every existing
# `from tools.rtm import normalize_ac_id` keeps resolving and no caller
# changed. Both are pure and never raise.
# --------------------------------------------------------------------------- #

_CANONICAL_AC_RE = re.compile(r"^AC-\d{3,}$")


def normalize_ac_id(raw: str | None) -> str:
    """Canonicalise an AC identifier so trace matching is robust to LLM/Jira
    formatting drift (QW-12 / I-059 / B-024).

    Upper-cases, strips spaces, and rewrites any ``AC``/``AC-``/``AC0`` + number
    form to the canonical ``AC-{N:03d}`` (so ``AC-1``, ``AC001``, ``ac-01`` all
    map to ``AC-001``). Non-matching values are returned upper-cased/stripped so
    unrelated ids still compare consistently. Never raises.
    """
    if not raw:
        return ""
    s = str(raw).strip().upper().replace(" ", "")
    m = re.match(r"^AC-?0*(\d+)$", s)
    if m:
        return f"AC-{int(m.group(1)):03d}"
    return s


# A case's requirement tag as a reader should see it, or "" when it has none.
#
# F06 (2026-08-19). Canonicalises an AC-SHAPED tag through ``normalize_ac_id``
# so the exported Requirement ID column and the Requirements Traceability sheet
# cannot name one criterion two ways ("AC-1" in the column, "AC-001" in the
# sheet, matched as the same thing).
#
# Anything NOT AC-shaped is returned stripped but otherwise UNTOUCHED:
# ``normalize_ac_id`` upper-cases and de-spaces every value it does not
# recognise, which is right for something only ever COMPARED and wrong for
# something rendered into a deliverable -- "Login must work" is not
# "LOGINMUSTWORK". Never raises.
def display_requirement_id(raw: str | None) -> str:
    """A case's requirement tag as a reader should see it, or "" when it has none."""
    # a display helper must never raise
    with never_raise(
        logger, "display_requirement_id failed -- returning empty", exc_info=True
    ):
        text = str(raw or "").strip()
        if not text:
            return ""
        canonical = normalize_ac_id(text)
        return canonical if _CANONICAL_AC_RE.match(canonical) else text
    return ""
