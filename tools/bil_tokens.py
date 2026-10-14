"""Placeholder / residual-token patterns, unresolved text and field caps."""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("tools.bilingual")

# Accepts ONE or TWO braces on each side. The prompt asks for double braces, but
# a model that emits `{EN:DM01}` used to slip straight through a `"{{" in value`
# guard and ship a raw token to the tester; matching both shapes turns that
# failure mode into a successful substitution instead.
PLACEHOLDER_RE = re.compile(
    r"\{\{?\s*(EN|AR)\s*:\s*([A-Za-z0-9_ -]{1,24}?)\s*\}\}?", re.IGNORECASE
)

# The unconditional backstop. Anything still brace-wrapped after substitution is
# a bug on OUR side, and a tester must never be handed `{{EN:DM01}}` in an
# Expected Results cell.
#
# SCOPE IS DELIBERATELY NARROW. The double-brace branch is unambiguous -- `{{x}}`
# is never legitimate test content. The single-brace branch is restricted to the
# `{EN:...}` / `{AR:...}` shape ON PURPOSE: a blanket `\{[^{}]*\}` also matches
# a JSON request body (`{"amount": 500}`) or a parameterised value (`{userId}`)
# in a Test Data cell, which the standing-rules API pack makes MORE likely, not
# less. Neutralising those would silently destroy real test data to fix a
# cosmetic leak -- a worse bug than the one being fixed.
RESIDUAL_TOKEN_RE = re.compile(
    r"\{\{[^{}\n]{0,120}\}\}|\{\s*(?:EN|AR)\s*:[^{}\n]{0,60}\}",
    re.IGNORECASE,
)

UNRESOLVED_TEXT = "(value not documented in the ticket - confirm with the BA)"

# TestCase.title is max_length=250 (tools/models.py:155) and module is
# max_length=100. A 200-char substituted message pushed into a title used to make
# TestCase.model_validate raise, which made the code keep the ORIGINAL,
# token-bearing case. Clamp instead.
_FIELD_CAPS = {"title": 250, "module": 100}


def clamp(value: str, field_name: str) -> str:
    cap = _FIELD_CAPS.get(field_name)
    if cap and len(value) > cap:
        logger.info(
            "bilingual substitution overflowed %s (%d > %d chars) - clamping",
            field_name,
            len(value),
            cap,
        )
        return value[: cap - 1].rstrip() + "…"
    return value


def _clamp(value: str, field_name: str) -> str:
    """Legacy name of :func:`clamp` (WX removes it)."""
    return clamp(value, field_name)
