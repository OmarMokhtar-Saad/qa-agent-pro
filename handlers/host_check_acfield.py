"""Acceptance-criteria field disclosure for the host-check report."""

from __future__ import annotations

import logging

from tools import settings_port
from tools.jira_mcp import ac_field_discovery_on

logger = logging.getLogger("tools.mcp_handlers")

_AC_NO_ACTION = (
    "- **No action needed.** A wrong id costs nothing: it is detected and "
    "discarded, and the criteria are read from the description instead. "
    "Setting `JIRA_AC_FIELD` to your instance's real Acceptance Criteria "
    "field is an accuracy hint, not a fix -- worth doing only if your "
    "tickets keep their criteria in a custom field rather than in the "
    "description or a use-case table."
)


# ``settings.jira_ac_field`` is a per-instance GUESS. It USED to default to
# ``customfield_10016``, which is a DATE field on at least one real workspace,
# and the timestamp it returned became a suite's only "acceptance criterion"
# -- which also suppressed the host job that would otherwise have derived real
# ones. The failure was invisible until someone read 98 generated cases.
#
# 2026-08-31: that default is GONE -- the field is unset unless an install
# names one, so the shipped behaviour no longer guesses. This section says so
# rather than warning about a default that no longer exists, and explains
# what unset MEANS, which is not "broken": the description scan, the use-case
# table and the host AC job all still run.
#
# Static text only: no ticket is fetched here, so this cannot fail or slow
# the report down.
def ac_field_section() -> list[str]:
    """Disclose which Jira field is configured as the acceptance-criteria source."""
    try:
        raw_field = str(getattr(settings_port.get(), "jira_ac_field", "") or "").strip()
        out = [
            "### Acceptance-criteria field",
            "",
        ]
        out += _ac_field_configured_lines(raw_field)
        out.append(_ac_field_discovery_line(ac_field_discovery_on()))
        out.append(_AC_NO_ACTION)
        return out
    except Exception:
        logger.exception("ac_field_section failed - omitting it")
        return []


def _ac_field_configured_lines(raw_field: str) -> list[str]:
    """The 'Configured field' bullets for a set or unset JIRA_AC_FIELD."""
    if raw_field:
        return [
            f"- Configured field: `{raw_field}` (`JIRA_AC_FIELD`)",
            "- This id differs per Jira instance. It is used ONLY when its "
            "value reads like requirement text -- a date, a bare number or a "
            "single token is rejected, because on one workspace a guessed id "
            "turned out to be a DATE field and the timestamp became the "
            "suite's only acceptance criterion.",
        ]
    return [
        "- Configured field: **none** (`JIRA_AC_FIELD` is unset -- the "
        "shipped default since 2026-08-31).",
        "- This is not a gap. No id is guessed, so nothing is requested "
        "from Jira that this install has not named, and no wrong field's "
        "value can be mistaken for requirements. Criteria come from the "
        "ticket description and its use-case table, and your chat model "
        "derives them when neither yields any.",
    ]


def _ac_field_discovery_line(discovery: bool) -> str:
    """The custom-field discovery bullet."""
    if discovery:
        return (
            "- Custom-field discovery is **on**: when the configured field "
            "holds nothing usable, other custom fields are searched for one "
            "whose value reads like requirements. The choice is logged."
        )
    return (
        "- Custom-field discovery is off (default). When the configured "
        "field holds nothing usable, the ticket description is parsed "
        "instead -- an 'Acceptance Criteria' heading, or a use-case "
        "table. If neither yields anything, your chat model is asked to "
        "derive the criteria, so traceability still works."
    )
