"""Host mode orchestration: expected category names, count bounds and acceptance-criteria job entries."""

from __future__ import annotations

import logging

from agents.test_scenario_agent import _case_count_bounds
from config.settings_guard import never_raise
from tools.untrusted import wrap_untrusted

logger = logging.getLogger("agents.host_mode")


def expected_category_names(prepared) -> list:
    """Canonical category names from prepared.categories (name is tuple[0]).
    Never raises; returns []."""
    with never_raise(logger, "expected_category_names failed", exc_info=True):
        out = []
        for entry in getattr(prepared, "categories", None) or []:
            if isinstance(entry, (list, tuple)) and entry:
                name = str(entry[0] or "").strip()
            elif isinstance(entry, dict):
                name = str(entry.get("name") or "").strip()
            else:
                name = ""
            if name:
                out.append(name)
        return out
    return []


# (min_cases, max_cases) THIS prep demands PER CATEGORY.
#
# The single readable form of the derivation build_prepare_payload (and
# build_category_job) already makes inline: _case_count_bounds over the same
# complexity proxy, in the same precedence. Lifted out so
# handlers can STAMP the floor into the prep envelope at prepare
# time without importing a private agent symbol.
#
# It HAS to be stamped: ``prepared.categories`` is a list of
# ``(name, focus, preferred_type)`` tuples carrying no counts, so nothing at
# submit time can recover what the payload asked for.
#
# Never raises; returns ``(0, 0)`` when the bounds cannot be derived, which
# callers must read as "no floor is known" rather than "the floor is zero".
def prepared_case_bounds(prepared) -> "tuple[int, int]":
    """(min_cases, max_cases) THIS prep demands PER CATEGORY."""
    with never_raise(
        logger, "prepared_case_bounds failed", level=logging.WARNING, exc_info=True
    ):
        lo, hi = _case_count_bounds(
            prepared.complexity_text or prepared.feature_text or prepared.user_msg,
            prepared.ui_content,
        )
        return int(lo), int(hi)
    return 0, 0


# WRAPPED, like every other Jira-sourced block that reaches a model.
# The value is already spoof-stripped and capped by
# `rtm.sanitize_ac_description`, so it cannot forge a delimiter --
# what it could do was arrive UNLABELLED next to code-authored
# instructions, which is the ordinary-injection half of audit F1.
# `wrap_untrusted` is idempotent against the sanitiser (it strips the
# same tags), so a host that echoes this field back cannot
# double-wrap it. The ac_id stays bare: it is server-generated
# (`AC-%03d`) or `^AC-\d{3}$`-gated, never attacker-influenced, and
# it is the value `requirement_id` is matched on.
def _ac_entry(ac) -> dict | None:
    """One job-packet entry for an acceptance criterion, or None to skip it."""
    ac_id = str(getattr(ac, "ac_id", "") or "").strip()
    desc = str(getattr(ac, "description", "") or "").strip()
    if not ac_id or not desc:
        return None
    return {
        "ac_id": ac_id,
        "description": wrap_untrusted(
            "jira_acceptance_criteria", desc, limit=len(desc) + 1
        ),
    }


# The SERVER-KNOWN acceptance criteria as job-packet entries. Never raises.
#
# Read from ``prepared.acs`` -- the SAME list ``rtm_hint`` is rendered from
# (``format_ac_prompt_block(acs)`` in ``_prepare_generation``), so the
# structured field and the system prompt can never disagree about which ids
# exist. That agreement is the whole point: the packet used to hardcode ``[]``
# while its own system_prompt listed AC-001..AC-00N, and a literal-minded host
# model following the structured field nulls every requirement_id and silently
# destroys the RTM (live repro 2026-08-15, prep 4931b9c5ad084e918ff2b6dd5f025433).
#
# Non-empty whenever the server parsed criteria at prepare time: the Jira AC
# field, the description fallback, or (since 2026-08-15) a pasted feature text
# carrying its own "Acceptance Criteria" heading.
#
# Empty ONLY when the server genuinely has none -- the AC_JOB boomerang case,
# where the PARENT derives the list and fills this field before dispatch.
def _prepared_ac_entries(prepared) -> list[dict]:
    """The SERVER-KNOWN acceptance criteria as job-packet entries. Never raises."""
    with never_raise(
        logger, "_prepared_ac_entries failed", level=logging.WARNING, exc_info=True
    ):
        entries = [_ac_entry(ac) for ac in getattr(prepared, "acs", None) or []]
        return [e for e in entries if e is not None]
    return []
