"""Tolerant exact matches for resolve-or-ask (leaf of resolve_match)."""

from __future__ import annotations

import re
from typing import Sequence

from tools.mobile.resolve_types import Candidate


def squash(text: object) -> str:
    """Lower-case letters and digits only: case, spaces and punctuation vanish."""
    return "".join(re.findall(r"[a-z0-9]+", str(text or "").lower()))


def tolerant_exact(text: str, pool: Sequence[Candidate]) -> list:
    """For a hint of two or more words: the candidates whose label or whole key
    equals it ignoring case, spaces and punctuation ("Acme QA" == "acme-qa"),
    else those whose key ENDS with it ("acme qa" -> ``com.x.acme.qa``).
    A one-word hint matches nothing here: it keeps every flavour so the tester
    picks."""
    if len(re.findall(r"[a-z0-9]+", str(text).lower())) < 2:
        return []
    want = squash(text)
    same = [c for c in pool if want in (squash(c.label), squash(c.key))]
    if same:
        return same
    return [c for c in pool if squash(c.key).endswith(want)]
