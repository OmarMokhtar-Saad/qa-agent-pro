"""Detection of Arabic literals typed straight into generated cases."""

from __future__ import annotations

import logging

from tools import bil_key
from tools.bil_pair import LanguagePair
from tools.models import TestCase

logger = logging.getLogger("tools.bilingual")


def _case_text(tc: TestCase) -> str:
    chunks = [getattr(tc, "title", "") or ""]
    for step in getattr(tc, "steps", None) or []:
        chunks.append(getattr(step, "action", "") or "")
        chunks.append(getattr(step, "expected_result", "") or "")
        chunks.append(getattr(step, "test_data", "") or "")
    return "\n".join(chunks)


# This is the documented LLM-compliance failure mode: the model ignores the
# placeholder instruction and reproduces the string itself, which is exactly
# what the placeholder mechanism exists to prevent. Flag only -- the text may
# still be correct, but it was NOT mechanically carried through, so a human
# has to diff it against the ticket. Never raises.
def detect_baked_literals(
    cases: list[TestCase], pairs: list[LanguagePair], covered: set[str]
) -> list[str]:
    """Keys whose Arabic literal was typed straight into a case (no placeholder)."""
    try:
        if not pairs or not cases:
            return []
        blob = "\n".join(_case_text(tc) for tc in cases)
        out: list[str] = []
        for p in pairs:
            key = bil_key.normalize_key(p.key)
            if not key or key in covered:
                continue
            if p.ar and len(p.ar) >= 4 and p.ar in blob:
                out.append(key)
        return sorted(set(out))
    except Exception:  # pragma: no cover - defensive
        logger.exception("detect_baked_literals failed - flagging nothing")
        return []
