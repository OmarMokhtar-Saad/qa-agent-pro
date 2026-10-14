"""Backstop sweep that neutralises any surviving placeholder token."""

from __future__ import annotations

import logging

from tools import bil_tokens
from tools.bil_tokens import RESIDUAL_TOKEN_RE, UNRESOLVED_TEXT
from tools.models import TestCase

logger = logging.getLogger("tools.bilingual")


def _step_sweep_update(step) -> dict:
    """The cleaned step fields, empty when the step carries no residual token."""
    s_update: dict = {}
    for fname in ("action", "test_data", "expected_result"):
        value = getattr(step, fname, None)
        if isinstance(value, str) and RESIDUAL_TOKEN_RE.search(value):
            s_update[fname] = RESIDUAL_TOKEN_RE.sub(UNRESOLVED_TEXT, value)
    return s_update


def _sweep_steps(tc: TestCase) -> list | None:
    new_steps = []
    steps_dirty = False
    for step in getattr(tc, "steps", None) or []:
        s_update = _step_sweep_update(step)
        if s_update:
            steps_dirty = True
            new_steps.append(step.model_copy(update=s_update))
        else:
            new_steps.append(step)
    return new_steps if steps_dirty else None


def _case_sweep_update(tc: TestCase) -> dict:
    """The model_copy update that neutralises residual tokens; empty when clean."""
    update: dict = {}
    for fname in ("title", "preconditions", "postconditions", "module"):
        value = getattr(tc, fname, None)
        if isinstance(value, str) and RESIDUAL_TOKEN_RE.search(value):
            update[fname] = bil_tokens._clamp(
                RESIDUAL_TOKEN_RE.sub(UNRESOLVED_TEXT, value), fname
            )
    new_steps = _sweep_steps(tc)
    if new_steps is not None:
        update["steps"] = new_steps
    return update


def _sweep_all(cases: list[TestCase]) -> tuple[list[TestCase], list[str]]:
    out: list[TestCase] = []
    dirty: list[str] = []
    for tc in cases:
        update = _case_sweep_update(tc)
        if update:
            dirty.append(tc.tc_id)
            out.append(tc.model_copy(update=update))
        else:
            out.append(tc)
    return out, dirty


def _log_dirty(dirty: list[str]) -> None:
    logger.warning(
        "residual placeholder sweep neutralised token(s) in %d case(s): %s "
        "- the bilingual substitution did not fully resolve",
        len(dirty),
        ", ".join(dirty[:10]),
    )


# Replace ANY surviving ``{{...}}`` / ``{EN:KEY}`` token with UNRESOLVED_TEXT.
#
# The unconditional backstop for the three ways a token can survive
# substitution: a re-validation failure that kept the original case, a token
# shape the substituter did not recognise, and an exception inside the pack.
# Rewrites via ``model_copy(update=...)``, which does NOT re-run pydantic
# validators (the same property the pipeline's TC renumber relies on --
# agents/test_scenario_agent.py:2153-2157), so the sweep can never itself fail
# on a length or pattern constraint.
#
# Returns ``(cases, [tc_id, ...])`` -- the ids that needed cleaning. Never
# raises; on failure the input list comes back untouched.
def sweep_residual_placeholders(
    cases: list[TestCase],
) -> tuple[list[TestCase], list[str]]:
    """Neutralise residual placeholder tokens; returns (cases, dirty tc_ids)."""
    try:
        if not cases:
            return list(cases or []), []
        out, dirty = _sweep_all(cases)
        if dirty:
            _log_dirty(dirty)
        return out, dirty
    except Exception:
        logger.exception("sweep_residual_placeholders failed - cases unchanged")
        return list(cases), []
