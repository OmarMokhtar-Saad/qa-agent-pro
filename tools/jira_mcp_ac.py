"""Jira MCP: ac helpers (split from tools/jira_mcp.py)."""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("tools.jira_mcp")

_AC_HEADING_RE = re.compile(r"(?im)^[\s>#*_-]*acceptance\s+criteria\s*:?\s*$")


# A line only counts as the NEXT section heading when it carries a real heading
# signal -- otherwise an ordinary short AC line ("Account is locked") would be
# mistaken for a heading and cut the block short (NB-021).
_NEXT_HEADING_RE = re.compile(
    r"""(?mx)
    ^[\s>*_-]*(?:
        \#{1,6}\s*\S.{0,40}$          # markdown heading: leading #(s)
      | [A-Za-z][\w /-]{1,40}:\s*$    # label line ending in a colon
      | [A-Z0-9][A-Z0-9 /_-]{1,40}$   # ALL-CAPS label (no lowercase letters)
    )
    """
)


# A use-case table row: "| **Basic Flow** | user clicks cancel ... |". The label
# is bounded and the body is whatever remains up to the trailing pipe.
_UC_ROW_RE = re.compile(r"^\|\s*\**\s*([A-Za-z][A-Za-z /_-]{2,40}?)\s*\**\s*\|(.*)$")


# Only rows that carry a REQUIREMENT. Description/Actor/Pre-condition are context,
# not testable criteria, and including them produced restatements of the feature
# rather than things a case can verify.
_UC_AC_LABELS = frozenset(
    {
        "basic flow",
        "alternative flow",
        "alternate flow",
        "exception flow",
        "business rules",
        "business rule",
        "post-condition",
        "postcondition",
        "post condition",
        "acceptance criteria",
    }
)


# Confluence/Jira rich-text leaves these inline nodes in the markdown export.
_UC_CUSTOM_TAG_RE = re.compile(r"<custom\b[^>]*>.*?</custom>|<custom\b[^>]*/?>", re.S)


_UC_MAX_CRITERIA = 12


# 2026-08-31 (F2, corrected): 600 was the cap that actually cut TICKET-10051's
# acceptance criteria mid-word at "BR07: Upon" -- the block-level slice in
# agents/test_scenario_agent got the blame first, and raising it alone changed
# nothing, because a UC table puts EVERY business rule in ONE row and this cap
# is per row. Fourteen rules need roughly 2000 characters. The total stays
# bounded (_UC_MAX_CRITERIA rows) and the composed block is capped again, with
# a visible marker, by wrap_untrusted at settings.jira_max_ac_chars.
_UC_MAX_CRITERION_CHARS = 2500


def _uc_row_criterion(line: str) -> str:
    """One "Label: body" criterion from a UC table row, or "" for any other line."""
    m = _UC_ROW_RE.match(line.strip())
    if not m:
        return ""
    label = " ".join(m.group(1).split()).strip("*: ").casefold()
    if label not in _UC_AC_LABELS:
        return ""
    body = _UC_CUSTOM_TAG_RE.sub(" ", m.group(2))
    body = body.replace("|", " ").replace("*", "").replace("\\", "")
    body = " ".join(body.split())
    if not body:
        return ""
    return f"{m.group(1).strip('*: ').strip()}: {body[:_UC_MAX_CRITERION_CHARS]}"


# Pull acceptance criteria out of a USE-CASE TABLE description.
#
# 2026-08-03. ``_extract_ac_from_description`` only understands an "Acceptance
# Criteria" heading. A whole ticket family writes the requirements as a markdown
# UC table and has no such heading at all:
#
#     | **UC**              | **Cancel order**                          |
#     | **Basic Flow**      | User clicks cancel ... System displays ... |
#     | **Alternative Flow**| In Step 2, if the user clicks Keep ...     |
#     | **Business Rules**  | **BR02**: Not all products have ...       |
#
# That returned "" on the observed run, so the suite finalized with NO acceptance
# criteria and 61 of 98 cases had no ``requirement_id``.
#
# Emits ONE criterion per requirement-bearing row rather than trying to split a
# row into steps: the real rows are run-on prose ("User clicks on cancel order
# option System displays cancelation reason **DF01** User select a reason"), so
# sentence-splitting invents boundaries that are not in the ticket. One row per
# criterion is coarser but is actually what the ticket asserts.
#
# Context-only rows (Description / Actor / Pre-condition) are deliberately
# skipped -- they restate the feature instead of stating something checkable.
#
# Bounded and never raises: at most ``_UC_MAX_CRITERIA`` rows, each truncated to
# ``_UC_MAX_CRITERION_CHARS``. The caller still routes the result through the
# normal untrusted-text path.
def extract_ac_from_uc_table(description: str) -> str:
    """Pull acceptance criteria out of a USE-CASE TABLE description."""
    try:
        if not description:
            return ""
        out: list[str] = []
        for line in description.splitlines():
            criterion = _uc_row_criterion(line)
            if not criterion:
                continue
            out.append(criterion)
            if len(out) >= _UC_MAX_CRITERIA:
                break
        return "\n".join(out)
    except Exception:
        logger.exception("extract_ac_from_uc_table failed - returning empty")
        return ""


def _ac_block_after(rest: str) -> str:
    """The block under an 'Acceptance Criteria' heading, up to the next heading."""
    collected: list[str] = []
    for line in rest.splitlines():
        if (
            collected
            and _NEXT_HEADING_RE.match(line)
            and not re.match(r"^\s*[-*•\d]", line)
        ):
            break
        collected.append(line)
    return "\n".join(collected).strip()


# Fallback AC extraction: scan a ticket description for an 'Acceptance
# Criteria' heading and return the block beneath it (QW-11 / I-023).
#
# Empty string when no such heading is present. Never raises.
#
# With no "Acceptance Criteria" heading, try the use-case table shape before
# giving up -- a whole ticket family has its requirements only there, and
# returning "" left those suites with no traceability.
# Unconditional since 2026-08-12 (QA_JIRA_UC_TABLE_AC_ENABLED was
# deleted; it had been default ON since 2026-08-03). Failing toward
# the OLD behaviour would not have been the safe choice here either --
# the old behaviour is model-INVENTED criteria, not none.
def _extract_ac_from_description(description: str) -> str:
    """Fallback AC extraction: the block under an 'Acceptance Criteria' heading."""
    try:
        if not description:
            return ""
        m = _AC_HEADING_RE.search(description)
        if not m:
            return extract_ac_from_uc_table(description)
        return _ac_block_after(description[m.end() :].lstrip("\n"))
    except Exception:
        logger.exception("_extract_ac_from_description failed - returning empty")
        return ""
