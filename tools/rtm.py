"""Requirements Traceability Matrix helpers.

Parses acceptance criteria text into numbered AcceptanceCriterion items
and builds a markdown RTM coverage summary.

Never raises — all functions return empty results on failure.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from pydantic import BaseModel, Field

from config.settings import settings
from llm import ask_json
from tools.models import TestCase
from tools.untrusted import _GUARD, wrap_untrusted

logger = logging.getLogger(__name__)


@dataclass
class AcceptanceCriterion:
    ac_id: str
    description: str


class _GeneratedAC(BaseModel):
    description: str = Field(
        min_length=3, description="One testable acceptance criterion"
    )


class _GeneratedACList(BaseModel):
    acceptance_criteria: list[_GeneratedAC] = Field(default_factory=list)


_AC_GEN_SYSTEM = """\
You are a senior QA analyst. From the feature description, derive a concise list
of testable acceptance criteria — the observable conditions that must hold for
the feature to be considered correct.

Rules:
- 3 to 8 criteria, each a single, specific, verifiable statement.
- Cover the happy path, key negative/error cases, and important boundaries.
- Do NOT invent unrelated requirements; stay grounded in the description.
- Phrase each as an outcome (e.g. "A user with a valid token can access the page").
"""


async def generate_acs(feature_text: str) -> list[AcceptanceCriterion]:
    """Generate acceptance criteria from a plain-text feature description (T-11).

    Lets the RTM light up for the 3-of-4 input types that carry no explicit ACs.
    Returns numbered AcceptanceCriterion items, or [] on empty input / any failure.
    Never raises.
    """
    try:
        if not feature_text or not feature_text.strip():
            return []
        result = await ask_json(
            system=_AC_GEN_SYSTEM + _GUARD,
            user=wrap_untrusted("feature_description", feature_text),
            response_model=_GeneratedACList,
            model=settings.qa_classifier_model or None,
        )
        acs = [
            AcceptanceCriterion(ac_id=f"AC-{i:03d}", description=g.description.strip())
            for i, g in enumerate(result.acceptance_criteria, 1)
            if g.description.strip()
        ]
        logger.info("generate_acs: synthesized %d acceptance criteria", len(acs))
        return acs
    except Exception:
        logger.exception("generate_acs failed — returning empty list")
        return []


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


def parse_acceptance_criteria(raw: str) -> list[AcceptanceCriterion]:
    """Parse raw acceptance criteria text into numbered AcceptanceCriterion items.

    Handles:
    - Bulleted lists (-, *, •)
    - Numbered lists (1., 2., 1), 2))
    - Plain prose separated by blank lines
    - Plain prose separated by single newlines

    Returns [] on empty input or any exception.
    """
    try:
        if not raw or not raw.strip():
            return []

        # Split on bullet or numbered list markers at the start of a line,
        # or on double-newlines (paragraph breaks).
        lines = re.split(r"(?m)(?:^\s*[-*•]\s+|^\s*\d+[.)\]]\s+)|\n{2,}", raw)

        # If that produced only one non-empty chunk, fall back to single-newline split.
        non_empty = [ln.strip() for ln in lines if ln.strip()]
        if len(non_empty) <= 1:
            lines = raw.splitlines()

        items: list[str] = []
        for line in lines:
            line = line.strip()
            # Strip any residual leading list marker that survived the split.
            # A bare digit is CONTENT (e.g. "3 failed logins", "200ms"); only
            # strip a leading number when it is a real list marker — i.e. it is
            # immediately followed by a delimiter (./)/]) AND whitespace.
            line = re.sub(r"^\s*(?:[-*•]|\d+[.)\]])\s+", "", line).strip()
            if len(line) < 5:
                continue
            items.append(line)

        if not items:
            return []

        return [
            AcceptanceCriterion(ac_id=f"AC-{i:03d}", description=desc)
            for i, desc in enumerate(items, 1)
        ]
    except Exception:
        logger.exception("parse_acceptance_criteria failed — returning empty list")
        return []


def build_rtm_summary(
    acs: list[AcceptanceCriterion], test_cases: list[TestCase]
) -> str:
    """Build a markdown RTM coverage table and coverage stats.

    Returns empty string when acs is empty (no traceability data available).
    """
    if not acs:
        return ""

    # Map each AC ID to the TC IDs that reference it. Match on the *normalized*
    # id so a case tagged "AC-1"/"ac001" still traces to canonical "AC-001".
    ac_to_tcs: dict[str, list[str]] = {ac.ac_id: [] for ac in acs}
    norm_to_canonical: dict[str, str] = {
        normalize_ac_id(ac.ac_id): ac.ac_id for ac in acs
    }
    orphan_tc_ids: list[str] = []

    for tc in test_cases:
        canonical = norm_to_canonical.get(normalize_ac_id(tc.requirement_id))
        if canonical:
            ac_to_tcs[canonical].append(tc.tc_id)
        else:
            orphan_tc_ids.append(tc.tc_id)

    covered_count = sum(1 for tcs in ac_to_tcs.values() if tcs)
    total_count = len(acs)
    pct = int(covered_count / total_count * 100) if total_count else 0

    # Build table rows
    rows: list[str] = []
    for ac in acs:
        linked = ac_to_tcs[ac.ac_id]
        linked_str = ", ".join(linked) if linked else ""
        status = "Covered" if linked else "ORPHAN"
        desc = (
            ac.description[:80] + "..." if len(ac.description) > 80 else ac.description
        )
        rows.append(f"| {ac.ac_id} | {desc} | {linked_str} | {status} |")

    table = (
        "\n\n---\n\n"
        "## Requirements Traceability Matrix\n\n"
        "| AC ID | Acceptance Criterion | Linked TCs | Status |\n"
        "|-------|----------------------|------------|--------|\n" + "\n".join(rows)
    )

    coverage_line = (
        f"\n\n**Coverage: {covered_count} of {total_count} ACs covered ({pct}%)."
    )
    if total_count - covered_count > 0:
        coverage_line += f" {total_count - covered_count} orphan AC(s) flagged.**"
    else:
        coverage_line += " All ACs covered.**"

    orphan_tc_line = ""
    if orphan_tc_ids:
        orphan_tc_line = (
            "\n\n**Orphan test cases (no linked requirement): "
            + ", ".join(orphan_tc_ids[:20])
            + (" ..." if len(orphan_tc_ids) > 20 else "")
            + "**"
        )

    return table + coverage_line + orphan_tc_line


def rtm_oneline(acs: list[AcceptanceCriterion], test_cases: list[TestCase]) -> str:
    """Return a single-line RTM coverage stat (no table) for compact summaries.

    Returns empty string when acs is empty. Never raises.
    """
    try:
        if not acs:
            return ""
        ac_norm_ids = {normalize_ac_id(ac.ac_id) for ac in acs}
        covered_ids = {
            normalize_ac_id(tc.requirement_id)
            for tc in test_cases
            if normalize_ac_id(tc.requirement_id) in ac_norm_ids
        }
        covered = len(covered_ids)
        total = len(acs)
        orphans = total - covered
        line = f"\n\n**Requirements:** {covered}/{total} acceptance criteria traced"
        line += f", {orphans} orphan(s)." if orphans else ", all covered."
        return line
    except Exception:  # pragma: no cover - defensive, never break the summary
        logger.exception("rtm_oneline failed — returning empty string")
        return ""


def format_ac_prompt_block(acs: list[AcceptanceCriterion]) -> str:
    """Format ACs into a system-prompt block for LLM instruction.

    Returns empty string when acs is empty.
    """
    if not acs:
        return ""

    lines = "\n".join(f"- {ac.ac_id}: {ac.description}" for ac in acs)
    return (
        "\n\n## Acceptance Criteria (populate requirement_id)\n"
        "For each test case, set `requirement_id` to the ID of the AC it primarily validates.\n"
        "Use ONLY these AC IDs:\n"
        + lines
        + (
            "\nIf no AC applies to a test case, use JSON null for "
            "requirement_id — but prefer a real AC id: a case you cannot "
            "trace to any of the IDs above is usually testing something "
            "outside this ticket's scope.\n"
        )
    )
