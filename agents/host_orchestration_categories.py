"""Host mode orchestration: the per-category entries and the job stubs of the prepare payload."""

from __future__ import annotations

from typing import NamedTuple

from agents.host_orchestration_entries import _prepared_ac_entries
from agents.test_scenario_agent import (
    _CATEGORY_TASK_TEMPLATE,
    _QUALITY_RULES_UPFRONT,
    _case_count_bounds,
)


class CategoryCtx(NamedTuple):
    min_count: int
    max_count: int
    template: str
    quality: str


def _category_instruction(cat, ctx: CategoryCtx) -> str:
    """The per-category instruction: the task template plus the quality reminder."""
    name, focus, ptype = cat
    return (
        ctx.template.format(
            category_name=name,
            category_focus=focus,
            preferred_type=ptype,
            min_count=ctx.min_count,
            max_count=ctx.max_count,
        )
        + ctx.quality
    )


def _category_entry(cat, ctx: CategoryCtx) -> dict:
    """One category entry: name, focus, type, count bounds, instruction."""
    name, focus, ptype = cat
    return {
        "name": name,
        "focus": focus,
        "preferred_type": ptype,
        "min_cases": ctx.min_count,
        "max_cases": ctx.max_count,
        "instruction": _category_instruction(cat, ctx),
    }


def _prepare_categories(prepared) -> list:
    """The per-category entries: name, focus, type, count bounds, instruction."""
    min_count, max_count = _case_count_bounds(
        prepared.complexity_text or prepared.feature_text or prepared.user_msg,
        prepared.ui_content,
    )
    ctx = CategoryCtx(
        min_count, max_count, _CATEGORY_TASK_TEMPLATE, _QUALITY_RULES_UPFRONT
    )
    return [_category_entry(cat, ctx) for cat in prepared.categories]


def _prepare_job_stubs(prepared, prep_id: str, categories: list) -> list:
    """One job stub per category -- never a copy of user_context."""
    # The server-known criteria DO ride along (they are small, and a parent
    # dispatching straight from a stub must agree with the qa_get_category_job
    # packet and with the system prompt's AC block). The key is OMITTED when the
    # server has none, so the AC_JOB payload stays byte-identical.
    job_acs = _prepared_ac_entries(prepared)
    return [
        {
            "prep_id": prep_id or "",
            "category_name": c.get("name") or "",
            "instruction": c.get("instruction") or "",
            "min_cases": c.get("min_cases"),
            "max_cases": c.get("max_cases"),
            "preferred_type": c.get("preferred_type") or "",
            "focus": c.get("focus") or "",
            **({"acceptance_criteria": job_acs} if job_acs else {}),
        }
        for c in categories
    ]
