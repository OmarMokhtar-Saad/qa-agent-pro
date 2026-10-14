"""Jira MCP: ac field helpers (split from tools/jira_mcp.py)."""

from __future__ import annotations

import logging

from tools import settings_port
from tools.jira_mcp_adf import _as_text as as_text
from tools.rtm import looks_like_requirement_text

logger = logging.getLogger("tools.jira_mcp")


# Whether the configured AC custom field actually holds acceptance criteria.
#
# 2026-08-03. ``settings_port.get().jira_ac_field`` is a per-instance GUESS (default
# ``customfield_10016``). On a real workspace that id is a DATE field, so the AC
# block arrived as ``2025-09-11T09:07:21.362+0300``. Because the fallback was
# written as ``ac_src or _extract_ac_from_description(description)``, a non-empty
# timestamp WON the ``or`` and the ticket's real criteria -- which lived in the
# description -- were never parsed. Downstream, ``tools/rtm`` turned that
# timestamp into ``AC-001`` and 37 of 98 cases "traced" to it.
#
# ``rtm.looks_like_requirement_text`` now rejects that value, which stops the fake
# criterion, but on its own it only converts a WRONG AC into NO AC: the ``or``
# still suppresses the description. Truthiness is the wrong test, so ask the same
# question rtm asks downstream -- does any line look like a requirement? -- so the
# two cannot disagree about what counts as usable.
#
# Multi-line AC fields are common, and one junk line must not veto a real block,
# so ANY plausible line makes the field usable. On an unexpected error this
# returns today's truthiness answer: preferring a possibly-odd AC field is
# recoverable, silently discarding a real one is not.
def usable_ac_text(raw: object) -> bool:
    """Whether the configured AC custom field actually holds acceptance criteria."""
    text = "" if raw is None else str(raw)
    try:
        if not text.strip():
            return False
        return any(looks_like_requirement_text(ln) for ln in text.splitlines())
    except Exception:
        logger.exception("usable_ac_text failed - treating the field as usable")
        return bool(text.strip())


# Upper bound on how many custom fields discovery will consider, and the size of
# a value it will look at. Field maps on a mature Jira project run to hundreds of
# entries, all of it untrusted external content.
AC_DISCOVERY_MAX_FIELDS = 200


_AC_DISCOVERY_MAX_CHARS = 20000


_AC_DISCOVERY_MIN_CHARS = 40


def ac_field_discovery_on() -> bool:
    """The search of the OTHER custom fields. HARDCODED OFF since 2026-08-13.

    NOT settings-derived: QA_JIRA_AC_FIELD_DISCOVERY was DELETED (flag-surface
    reduction, batch 8a). Silently adopting the wrong field is the exact failure
    that made a DATE field's timestamp a suite's only acceptance criterion, and
    the search was never validated against a real workspace. Everything that
    made a mis-configured field survivable is UNAFFECTED and still runs: the
    ``usable_ac_text`` rejection, the description fallback, ``AC_JOB`` and the
    ``qa-doctor`` line naming the resolved field. The scan below is retained and
    still tested through this seam; reviving it is one line here.
    """
    return False


def _discovery_off_reason(raw: str) -> str:
    """Why the configured AC field was not used when discovery is off."""
    if raw:
        return (
            "configured field holds no requirement text "
            f"({raw[:40]!r}); discovery is off"
        )
    return "configured field is empty; discovery is off"


def _discover_ac_field(fields: dict, configured: str) -> tuple[str, str]:
    """(field_id, value) of the longest plausible other custom field, or ("", "")."""
    best_id = ""
    best_val = ""
    for index, (key, value) in enumerate(fields.items()):
        if index >= AC_DISCOVERY_MAX_FIELDS:
            break
        if not isinstance(key, str) or not key.startswith("customfield_"):
            continue
        if key == configured:
            continue
        candidate = as_text(value).strip()[:_AC_DISCOVERY_MAX_CHARS]
        if len(candidate) < _AC_DISCOVERY_MIN_CHARS:
            continue
        if not usable_ac_text(candidate):
            continue
        if len(candidate) > len(best_val):
            best_id, best_val = key, candidate
    return best_id, best_val


def _resolve_discovered(
    fields: dict, configured: str, raw: str
) -> tuple[str, str, str]:
    """The discovery branch of :func:`resolve_ac_field` (OFF by default)."""
    if not _ac_field_discovery_on():
        return configured, "", _discovery_off_reason(raw)
    best_id, best_val = _discover_ac_field(fields, configured)
    if best_val:
        logger.info(
            "AC-field discovery chose %s over the configured %s", best_id, configured
        )
        return best_id, best_val, f"discovered (configured {configured} unusable)"
    return configured, "", "no field holds requirement text"


# (field_id, raw_value, reason) for the acceptance-criteria source.
#
# ``settings_port.get().jira_ac_field`` is a per-instance GUESS -- its default,
# ``customfield_10016``, is a DATE field on the workspace this was found on, and
# the timestamp it returned became the suite's only "acceptance criterion".
# The configured field therefore wins ONLY when its value survives
# :func:`usable_ac_text`.
#
# When it does not, and :func:`ac_field_discovery_on` says so, the other
# custom fields are searched for one whose VALUE looks like requirement prose.
# Display names are not available to match on: the Atlassian MCP
# ``getJiraIssue`` response carries no ``names`` map, verified against a real
# payload. The longest plausible candidate wins, and the choice is logged.
#
# That search is HARDCODED OFF since 2026-08-13 (QA_JIRA_AC_FIELD_DISCOVERY was
# deleted) -- silently adopting the wrong field is the exact failure this whole
# change set exists to remove. :func:`qa-doctor` still shows what was resolved.
#
# Never raises: degrades to (configured_id, "", reason).
def resolve_ac_field(fields: object) -> tuple[str, str, str]:
    """(field_id, raw_value, reason) for the acceptance-criteria source."""
    configured = str(getattr(settings_port.get(), "jira_ac_field", "") or "")
    try:
        if not isinstance(fields, dict):
            return configured, "", "no fields in the payload"
        raw = as_text(fields.get(configured)).strip()
        if usable_ac_text(raw):
            return configured, raw, "configured field"
        return _resolve_discovered(fields, configured, raw)
    except Exception:
        logger.exception("resolve_ac_field failed - reporting no AC field")
        return configured, "", "resolution failed"


def _ac_field_discovery_on() -> bool:
    """Old private name (patched by foreign tests / outside callers); calls through."""
    return ac_field_discovery_on()
