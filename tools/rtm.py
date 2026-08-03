"""Requirements Traceability Matrix helpers.

Parses acceptance criteria text into numbered AcceptanceCriterion items
and builds a markdown RTM coverage summary.

Never raises — all functions return empty results on failure.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from dataclasses import field as dc_field

from pydantic import BaseModel, Field

from config.settings import settings
from llm import ask_json, server_llm_scope
from tools import token_meter
from tools.atomic_checklist import (
    HONESTY_BOUNDARY,
    lexical_cosine_matrix,
    provenance_caveats,
)
from tools.embeddings import backend_enabled, cosine_similarity, embed_texts
from tools.models import TestCase
from tools.untrusted import _GUARD, wrap_untrusted

logger = logging.getLogger(__name__)

# docs/LLM_MIGRATION_INVENTORY.md ledger id for the OPTIONAL entailment (b) and
# adjudication (c) tiers below. Named _NLI_ rather than generically, because
# this module owns a SECOND ledger call site
# (`rtm.acceptance_criteria`, the AC synthesis near the top of the file),
# tagged `_AC_LEDGER_ID` -- deliberately a different constant, since a bare
# _LEDGER_ID here would invite that call site to reuse the wrong tag.
#
# Ledger rule 4: the surviving direct call sites -- still reachable from
# graph.py, from evals/, and from any install that sets
# QA_HOST_CHECKLIST_NLI_SUPPRESS_ENABLED=false -- must TAG themselves, or the
# Phase-6 kill switch refuses them as UNTAGGED calls and the documented rollback
# ("set the flag false and the tiers come back") would silently degrade to tier
# (a) instead of restoring anything. Tagged, QA_SERVER_LLM_ALLOW=rtm.nli_verdicts
# keeps exactly this one path alive. No behavioural change while
# QA_SERVER_LLM_ENABLED is on (its default).
_NLI_LEDGER_ID = "rtm.nli_verdicts"


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


# Ledger id for the AC-synthesis call below (`rtm.acceptance_criteria`), the
# SECOND of this module's two ledger rows and deliberately a DISTINCT constant
# from _NLI_LEDGER_ID -- exactly as that constant's own comment instructs. The
# two rows migrate and are allow-listed independently.
#
# Ledger rule 4. The host route never reaches this call: handle_prepare_test_cases
# passes synthesize_acs=False (its `_host_ac` decision), so _run_gen_acs returns
# [] and agents/host_mode.AC_JOB derives the criteria on the tester's OWN model.
# What survives is the LEGACY route -- graph.py -> generate_test_scenarios ->
# _prepare_generation(synthesize_acs=True), plus evals/ -- and it is NOT deleted
# (Phase 3a's precedent). A surviving call must TAG itself or the Phase-6 kill
# switch refuses it as UNTAGGED, and QA_SERVER_LLM_ALLOW=rtm.acceptance_criteria
# would then allow nothing at all. Inert while QA_SERVER_LLM_ENABLED is on (its
# default): the scope only sets a ContextVar the guard reads when the flag is off.
_AC_LEDGER_ID = "rtm.acceptance_criteria"


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


async def generate_acs(
    feature_text: str, meter: object | None = None
) -> list[AcceptanceCriterion]:
    """Generate acceptance criteria from a plain-text feature description (T-11).

    Lets the RTM light up for the 3-of-4 input types that carry no explicit ACs.
    Returns numbered AcceptanceCriterion items, or [] on empty input / any failure.
    Never raises.

    ``meter`` is an optional tools.token_meter.TokenMeter; when passed, this
    call's tokens are recorded against it. Purely bookkeeping -- it never
    changes the prompt, the model, or the result.
    """
    try:
        if not feature_text or not feature_text.strip():
            return []
        _ac_user = wrap_untrusted("feature_description", feature_text)
        with server_llm_scope(_AC_LEDGER_ID):
            result = await ask_json(
                system=_AC_GEN_SYSTEM + _GUARD,
                user=_ac_user,
                response_model=_GeneratedACList,
                model=settings.qa_classifier_model or None,
            )
        token_meter.note(
            meter,
            "other",
            settings.qa_classifier_model or settings.qa_llm_model,
            system=_AC_GEN_SYSTEM,
            user=_ac_user,
            output_text=token_meter.model_text(result),
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


def _trace_map(
    acs: list[AcceptanceCriterion], test_cases: list[TestCase]
) -> tuple[dict, list]:
    """Map each AC id -> the tc_ids citing it, plus the cases citing nothing.

    Extracted so build_rtm_summary, rtm_trace and traceability_warning_section all
    read ONE computation instead of three traversals that could disagree. Match on
    the *normalized* id so a case tagged "AC-1"/"ac001" still traces to canonical
    "AC-001".
    """
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
    return ac_to_tcs, orphan_tc_ids


def rtm_trace(acs: list, test_cases: list) -> dict:
    """The traceability outcome as DATA, for the audit trail.

    build_rtm_summary has always PRINTED these numbers; nothing carried them out,
    so "is traceability degenerate?" needed a hand investigation. Never raises --
    an unreadable suite yields zeros rather than breaking a generation.
    """
    try:
        if not acs:
            return {"acs": 0, "covered": 0, "traced_cases": 0, "orphan_cases": 0}
        ac_to_tcs, orphan_tc_ids = _trace_map(acs, test_cases)
        return {
            "acs": len(acs),
            "covered": sum(1 for tcs in ac_to_tcs.values() if tcs),
            "traced_cases": sum(len(tcs) for tcs in ac_to_tcs.values()),
            "orphan_cases": len(orphan_tc_ids),
        }
    except Exception:
        logger.exception("rtm_trace failed -- returning zeros")
        return {"acs": 0, "covered": 0, "traced_cases": 0, "orphan_cases": 0}


def traceability_warning_section(acs: list, test_cases: list) -> str:
    """Escalate a DEGENERATE traceability outcome from a percentage to a finding.

    build_rtm_summary already prints "Coverage: 1 of 7 ACs covered (14%)". On the
    2026-07-29 and 2026-07-30 runs it did exactly that and nobody read it -- a
    percentage reads as a metric, not as a defect. This names it.

    Fires when more than one AC exists but at most ONE of them is cited.
    ``covered_count <= 1``, not ``== 1``: zero is strictly WORSE and is silent
    under an equality test -- and it has happened, when cases were tagged with
    checklist ids instead of AC ids.

    Counts are REAL, never "all N cases": a case citing nothing lands in
    orphan_tc_ids, so 1 traced case plus 64 orphans must not be reported as 65
    cases tracing to one AC. FLAG ONLY -- nothing is dropped or rewritten. States
    an observation, not an accusation: a legitimately small suite cannot cover 7
    ACs. Never raises.
    """
    try:
        if not acs or not test_cases:
            return ""
        ac_to_tcs, orphan_tc_ids = _trace_map(acs, test_cases)
        total = len(acs)
        covered = sum(1 for tcs in ac_to_tcs.values() if tcs)
        if total <= 1 or covered > 1:
            return ""
        head = "\n\n> \u26a0\ufe0f  **Requirement traceability looks degenerate.** "
        if covered == 0:
            body = (
                f"No test case traces to any of the {total} acceptance criteria "
                f"({len(orphan_tc_ids)} case(s) carry no usable `requirement_id`)."
            )
        else:
            cited = next((ac_id for ac_id, tcs in ac_to_tcs.items() if tcs), "")
            traced = sum(len(tcs) for tcs in ac_to_tcs.values())
            body = (
                f"{traced} case(s) trace to `{cited}` and {len(orphan_tc_ids)} "
                f"trace to nothing, out of {total} acceptance criteria "
                f"({total - covered} never referenced)."
            )
        return (
            head
            + body
            + " Traceability is unreliable for this suite: the RTM above cannot "
            "tell you which requirements are actually tested. Re-check the "
            "`requirement_id` on each case against the AC list."
        )
    except Exception:
        logger.exception("traceability_warning_section failed -- returning empty")
        return ""


def build_rtm_summary(
    acs: list[AcceptanceCriterion], test_cases: list[TestCase]
) -> str:
    """Build a markdown RTM coverage table and coverage stats.

    Returns empty string when acs is empty (no traceability data available).
    """
    if not acs:
        return ""

    ac_to_tcs, orphan_tc_ids = _trace_map(acs, test_cases)
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


# --------------------------------------------------------------------------- #
# Bidirectional checklist matcher (Batch 2, Pass 3)
#
# WHY THIS EXISTS: build_rtm_summary above matches TC -> AC via
# ``tc.requirement_id``, which the GENERATING model self-assigns — it marks its
# own homework, and normalize_ac_id only fuzzy-repairs that self-tagging. The
# matcher below is EXTERNAL and DETERMINISTIC: it compares each requirement's
# text against each case's title + expected results, and never reads
# ``requirement_id`` at all.
#
# Three tiers, cheapest first:
#   (a) embedding cosine via tools/embeddings (TF-IDF lexical fallback, pure
#       stdlib, when the backend is disabled or fails);
#   (b) OPTIONAL batched entailment judgement over the ambiguous middle band
#       (QA_CHECKLIST_NLI_ENABLED, default OFF);
#   (c) OPTIONAL batched adjudication over ONLY what (a)+(b) could not separate
#       (QA_CHECKLIST_ADJUDICATE_ENABLED, default OFF).
#
# DEVIATION FROM THE RESEARCH, DELIBERATE: tiers (b)/(c) do NOT use a
# fine-tuned BERT-large NLI model. Adding a transformer dependency would break
# the "no new embedding dependency" constraint and the optional-extra install
# story, and the cited paper reports no latency/cost figures. Instead both tiers
# are compact BATCHED ``llm.ask_json`` calls with a strict, non-generative
# judging prompt and a DIFFERENT system prompt from the generator — so the
# generating model still never marks its own homework — bounded by
# QA_CHECKLIST_MAX_PAIRS. Both default OFF, so the default path is embeddings
# (or lexical) only and costs ZERO extra LLM calls.
#
# THREE CORRECTNESS INVARIANTS, each of which was a real defect risk:
#   1. TRUNCATION IS NOT A GAP. ``presented_item_ids`` names the items that
#      actually reached the generator. Anything else goes to
#      ``not_presented_item_ids`` and is EXCLUDED from coverage_pct / gap_rate.
#   2. A DEGRADED RUN PUBLISHES NO PERCENTAGE. Lexical TF-IDF cosine is not on
#      the same scale as embedding cosine, so ``checklist_tally_line`` prints
#      "UNRELIABLE (lexical fallback)" with the percentage suppressed instead of
#      a bold number nobody should trust.
#   3. THE MATRIX NEVER RUNS ON THE EVENT LOOP. items x cases pure-Python
#      cosines (up to 200 x 80) are built inside ``asyncio.to_thread`` so the
#      MCP stdio loop keeps serving.
# --------------------------------------------------------------------------- #

# Lexical TF-IDF cosine lives on a different scale than embedding cosine, so the
# operator-tunable embedding thresholds must NOT be applied to it. These fixed
# lexical thresholds are used instead, a lexical match is capped at MEDIUM
# confidence (never HIGH), and the tally suppresses the percentage entirely.
_LEXICAL_HIGH = 0.45
_LEXICAL_LOW = 0.15

# Above this many (items x cases) cells the matrix build is slow enough to be
# worth telling the operator about. It is NOT a drop threshold — dropping pairs
# would silently understate coverage; the work is simply offloaded to a thread.
_MATRIX_CELL_WARN = 20000

_DEGRADED_NOTE = (
    "No embeddings backend was available, so requirement matching used the "
    "pure-lexical TF-IDF fallback. TF-IDF cosine between an EARS requirement and "
    "a test case rarely clears the match threshold for a genuine paraphrase, so "
    "the numbers below UNDERSTATE coverage and NO percentage is reported. Set "
    "QA_EMBEDDINGS_BACKEND (local or voyage) and re-run for a usable audit."
)


@dataclass
class MatchLink:
    """One requirement -> test-case link produced by the external matcher."""

    item_id: str
    tc_id: str
    score: float
    confidence: str  # HIGH | MEDIUM | LOW
    tier: str  # embeddings | lexical | entailment | adjudication


@dataclass
class ChecklistCoverage:
    """Bidirectional coverage result. Never carries an exception.

    ``total_items`` is the WHOLE checklist; ``presented_items`` is how many of
    them reached the generator. coverage_pct / gap_rate are computed over
    ``presented_items`` only — see ``not_presented_item_ids``."""

    total_items: int = 0
    presented_items: int = 0
    total_cases: int = 0
    links: list = dc_field(default_factory=list)
    covered_item_ids: list = dc_field(default_factory=list)
    gap_item_ids: list = dc_field(default_factory=list)
    not_presented_item_ids: list = dc_field(default_factory=list)
    orphan_tc_ids: list = dc_field(default_factory=list)
    confidence_counts: dict = dc_field(default_factory=dict)
    coverage_pct: float = 0.0
    gap_rate: float = 0.0
    orphan_rate: float = 0.0
    tier_used: str = ""
    degraded: bool = False
    notes: list = dc_field(default_factory=list)
    ran: bool = False


class _PairVerdict(BaseModel):
    pair_id: int = Field(default=-1, description="The pair id you were given")
    verdict: str = Field(default="unsure", description="entails | contradicts | unsure")


class _PairVerdicts(BaseModel):
    verdicts: list[_PairVerdict] = Field(default_factory=list)


class _Adjudication(BaseModel):
    pair_id: int = Field(default=-1)
    covered: bool = Field(default=False)
    reason: str = Field(default="")


class _Adjudications(BaseModel):
    decisions: list[_Adjudication] = Field(default_factory=list)


_ENTAILMENT_SYSTEM = """\
You are a requirements-traceability judge. You are given numbered PAIRS. Each
pair has a REQUIREMENT (one atomic, independently-verifiable outcome) and a TEST
(its title plus its expected results).

For every pair answer ONE question: does executing the TEST verify the
REQUIREMENT — i.e. would the test FAIL if the requirement were not implemented?

verdict values:
  "entails"     - yes, the test's expected results verify this exact outcome.
  "contradicts" - the test is about a different outcome.
  "unsure"      - genuinely undecidable from the text you were given.

Judge ONLY the text provided; never invent behaviour. Do not be generous: a test
that merely mentions the same screen, field or feature does NOT entail the
requirement unless its expected result asserts that requirement's outcome.
Return exactly one verdict for every pair id you were given.
Output STRICTLY the JSON object for the schema, nothing else.
"""

_ADJUDICATION_SYSTEM = """\
You are the final adjudicator for requirement-to-test traceability. You see only
the pairs that an embedding matcher and an entailment judge could NOT separate.

For each pair decide strictly: is this requirement verified by this test?
Default to false. Answer true only when the test's expected result asserts the
requirement's outcome. Give a one-line reason.

Your true verdicts are reported to the tester as LOW-confidence matches that
REQUIRE human review, so a wrong true is worse than a wrong false.
Output STRICTLY the JSON object for the schema, nothing else.
"""


def _item_text(item) -> str:
    return f"{getattr(item, 'text', '') or ''}".strip()


def _case_match_payload(tc: TestCase) -> str:
    """What the matcher reads for a case: title + every expected result.

    The expected results are the verifiable claims — matching on step ACTIONS
    would reward navigation boilerplate shared by every case. Bounded so a
    pathological case cannot blow up the embedding payload."""
    try:
        expected = " ".join((s.expected_result or "").strip() for s in (tc.steps or []))
        return f"{(tc.title or '').strip()} || {expected}".strip()[:1200]
    except Exception:
        return (getattr(tc, "title", "") or "").strip()[:1200]


def _cosine_matrix_sync(item_vectors: list, case_vectors: list) -> list:
    """Pure-Python O(items x cases) cosine matrix. ALWAYS called through
    ``asyncio.to_thread`` — 200 x 80 x 384-dim on the event loop would stall the
    MCP stdio server for about a second, and the checklist-remediation loop can
    call the matcher once per round."""
    return [[cosine_similarity(a, b) for b in case_vectors] for a in item_vectors]


async def _similarity_matrix(item_texts: list[str], case_texts: list[str]) -> tuple:
    """(matrix, tier, degraded). Tier (a). Never raises.

    ONE batched embed_texts call covering both sides, then the cosine matrix
    built OFF the event loop. Falls back to the stdlib TF-IDF matrix (also off
    the event loop) whenever embeddings are disabled, error out, or return a
    mismatched vector count."""
    cells = len(item_texts) * len(case_texts)
    if cells > _MATRIX_CELL_WARN:
        logger.info(
            "checklist matcher: building a %d x %d similarity matrix (%d cells) "
            "in a worker thread",
            len(item_texts),
            len(case_texts),
            cells,
        )
    try:
        if backend_enabled():
            emb = await embed_texts(list(item_texts) + list(case_texts))
            vectors = emb.get("content") if isinstance(emb, dict) else None
            if (
                not emb.get("error")
                and vectors
                and len(vectors) == len(item_texts) + len(case_texts)
            ):
                iv = vectors[: len(item_texts)]
                cv = vectors[len(item_texts) :]
                matrix = await asyncio.to_thread(_cosine_matrix_sync, iv, cv)
                return matrix, "embeddings", False
            logger.info(
                "checklist matcher: embeddings unavailable (%s) — using the "
                "lexical TF-IDF fallback",
                (emb or {}).get("error") if isinstance(emb, dict) else "no content",
            )
    except Exception:
        logger.exception("checklist matcher: embedding tier failed — going lexical")
    try:
        matrix = await asyncio.to_thread(
            lexical_cosine_matrix, list(item_texts), list(case_texts)
        )
    except Exception:
        logger.exception("checklist matcher: lexical tier failed — scoring zeros")
        matrix = [[0.0] * len(case_texts) for _ in item_texts]
    return matrix, "lexical", True


async def _entailment_pass(pairs: list[tuple]) -> dict:
    """Tier (b). ``pairs`` is [(pair_id, requirement_text, case_text), ...].

    Returns {pair_id: "entails"|"contradicts"|"unsure"}. Returns {} (all pairs
    left unresolved) when the flag is OFF or on any failure. Never raises."""
    try:
        if not pairs or not getattr(settings, "qa_checklist_nli_enabled", False):
            return {}
        body = "\n\n".join(
            f"PAIR {pid}\nREQUIREMENT: {req}\nTEST: {case}" for pid, req, case in pairs
        )
        with server_llm_scope(_NLI_LEDGER_ID):
            result: _PairVerdicts = await ask_json(
                system=_ENTAILMENT_SYSTEM + _GUARD,
                user=wrap_untrusted("requirement_test_pairs", body, limit=20000),
                response_model=_PairVerdicts,
                model=settings.qa_classifier_model or None,
            )
        out: dict = {}
        for v in result.verdicts:
            verdict = (v.verdict or "").strip().lower()
            if verdict in ("entails", "contradicts", "unsure"):
                out[int(v.pair_id)] = verdict
        return out
    except Exception:
        logger.warning(
            "checklist matcher: entailment tier failed — leaving the band unresolved",
            exc_info=True,
        )
        return {}


async def _adjudication_pass(pairs: list[tuple]) -> dict:
    """Tier (c). Same input shape as tier (b). Returns {pair_id: bool}.

    Returns {} when the flag is OFF or on any failure. Never raises."""
    try:
        if not pairs or not getattr(settings, "qa_checklist_adjudicate_enabled", False):
            return {}
        body = "\n\n".join(
            f"PAIR {pid}\nREQUIREMENT: {req}\nTEST: {case}" for pid, req, case in pairs
        )
        with server_llm_scope(_NLI_LEDGER_ID):
            result: _Adjudications = await ask_json(
                system=_ADJUDICATION_SYSTEM + _GUARD,
                user=wrap_untrusted("requirement_test_pairs", body, limit=20000),
                response_model=_Adjudications,
                model=settings.qa_classifier_model or None,
            )
        return {int(d.pair_id): bool(d.covered) for d in result.decisions}
    except Exception:
        logger.warning(
            "checklist matcher: adjudication tier failed — leaving the band unresolved",
            exc_info=True,
        )
        return {}


async def match_checklist(
    items: list,
    test_cases: list[TestCase],
    presented_item_ids: list | None = None,
    allow_llm_tiers: bool = True,
) -> ChecklistCoverage:
    """Bidirectional, EXTERNAL requirement <-> test-case matching (Pass 3).

    FORWARD: every PRESENTED checklist item is either linked to at least one
    case, or reported as a first-class gap (rendered as ``NOT COVERED``) — never
    silently dropped. BACKWARD: every case that links to nothing is reported as
    an orphan (``REVIEW_REQUIRED``) — this generalises qa_ac_anchoring_enforce
    from string-id matching to semantic matching, but it FLAGS ONLY: nothing is
    ever dropped, because TraceLLM reports precision around 0.55 for this class
    of matcher and dropping on a false negative would destroy real coverage.

    ``presented_item_ids``: the ids that actually fitted into the generator's
    prompt (``atomic_checklist.format_checklist_prompt_block`` returns them).
    ``None`` means "all of them". Anything NOT in this set is reported under
    ``not_presented_item_ids`` and is excluded from coverage_pct / gap_rate: the
    generator was never asked to cover it, so counting it as a gap would report
    our own prompt truncation as a requirements failure.

    ``allow_llm_tiers=False`` restricts the matcher to tier (a). The
    checklist-remediation loop uses it so that up to three in-loop matcher calls
    cannot each fire the optional entailment/adjudication calls (that would turn
    "up to 2 extra ask_json calls" into up to 8).

    Never raises — any failure returns a coverage object with ``ran=False`` so
    every caller degrades to today's behaviour."""
    cov = ChecklistCoverage()
    try:
        if not items or not test_cases:
            return cov

        if presented_item_ids is None:
            scored = list(items)
            not_presented: list = []
        else:
            allowed = {str(x) for x in presented_item_ids}
            scored = [it for it in items if it.item_id in allowed]
            not_presented = [it.item_id for it in items if it.item_id not in allowed]
        if not scored:
            # Nothing reached the generator — report that, do NOT report 0%.
            cov.ran = True
            cov.total_items = len(items)
            cov.presented_items = 0
            cov.total_cases = len(test_cases)
            cov.not_presented_item_ids = not_presented
            cov.orphan_tc_ids = [tc.tc_id for tc in test_cases]
            cov.orphan_rate = 100.0
            cov.confidence_counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
            cov.notes.append(
                "NONE of the checklist requirements fitted into the generator "
                "prompt (QA_CHECKLIST_MAX_PROMPT_CHARS is too small), so no "
                "coverage percentage can be reported."
            )
            return cov

        item_texts = [_item_text(it) for it in scored]
        case_texts = [_case_match_payload(tc) for tc in test_cases]
        matrix, tier, degraded = await _similarity_matrix(item_texts, case_texts)

        if degraded:
            high = _LEXICAL_HIGH
            low = _LEXICAL_LOW
            cov.notes.append(_DEGRADED_NOTE)
        else:
            high = float(getattr(settings, "qa_checklist_match_high", 0.75) or 0.75)
            low = float(getattr(settings, "qa_checklist_match_low", 0.30) or 0.30)
        if low > high:
            low, high = high, low

        if not_presented:
            cov.notes.append(
                f"{len(not_presented)} requirement(s) did not fit into the "
                "generator prompt (QA_CHECKLIST_MAX_PROMPT_CHARS) and were never "
                "shown to the model. They are listed under NOT PRESENTED TO "
                "GENERATOR and are EXCLUDED from the coverage percentage and the "
                "gap rate — they are a configuration issue, not a coverage gap. "
                "Raise QA_CHECKLIST_MAX_PROMPT_CHARS (or lower "
                "QA_CHECKLIST_MAX_ITEMS) and re-run. Note that a test written for "
                "one of them can appear here as an orphan."
            )

        links: list[MatchLink] = []
        band: list[tuple] = []  # (pair_id, req_text, case_text)
        band_meta: dict = {}  # pair_id -> (i, j, score)
        pair_id = 0
        max_pairs = int(getattr(settings, "qa_checklist_max_pairs", 40) or 40)
        tiers_on = bool(allow_llm_tiers)
        # Phase 3b: `notes` is this module's OWN established channel for "this
        # measurement was degraded" (see _DEGRADED_NOTE) and is the only one
        # that survives into render_checklist_section, coverage_to_dict, the
        # XLSX checklist sheets and the suite_store payload. Without this the
        # suppression would exist only in the ephemeral chat reply and the
        # EXPORTED artifact would silently look like a full-strength
        # measurement. Only emitted when a tier was genuinely turned off, i.e.
        # when it would otherwise have run.
        if not tiers_on and (
            getattr(settings, "qa_checklist_nli_enabled", False)
            or getattr(settings, "qa_checklist_adjudicate_enabled", False)
        ):
            cov.notes.append(
                "The OPTIONAL entailment / adjudication tiers were NOT run for "
                "this measurement, so the ambiguous similarity band is reported "
                "as uncovered instead of being re-judged and the coverage "
                "figure may UNDERSTATE real coverage. On a host-mode submit "
                "this is deliberate (QA_HOST_CHECKLIST_NLI_SUPPRESS_ENABLED, "
                "default on): those tiers are only worth something when a model "
                "OTHER than the one that wrote the cases re-judges them, and in "
                "host mode the generator is the chat model itself. Set "
                "QA_HOST_CHECKLIST_NLI_SUPPRESS_ENABLED=false to restore them."
            )

        for i, row in enumerate(matrix):
            for j, score in enumerate(row):
                if score >= high:
                    links.append(
                        MatchLink(
                            item_id=scored[i].item_id,
                            tc_id=test_cases[j].tc_id,
                            score=float(score),
                            confidence="MEDIUM" if degraded else "HIGH",
                            tier=tier,
                        )
                    )
                elif score >= low and tiers_on and len(band) < max_pairs:
                    band.append((pair_id, item_texts[i], case_texts[j]))
                    band_meta[pair_id] = (i, j, float(score))
                    pair_id += 1

        entail = await _entailment_pass(band)
        unresolved: list[tuple] = []
        for pid, req, case in band:
            verdict = entail.get(pid)
            i, j, score = band_meta[pid]
            if verdict == "entails":
                links.append(
                    MatchLink(
                        item_id=scored[i].item_id,
                        tc_id=test_cases[j].tc_id,
                        score=score,
                        confidence="MEDIUM",
                        tier="entailment",
                    )
                )
            elif verdict == "contradicts":
                continue
            else:
                unresolved.append((pid, req, case))

        adjudicated = await _adjudication_pass(unresolved)
        for pid, _req, _case in unresolved:
            if adjudicated.get(pid):
                i, j, score = band_meta[pid]
                links.append(
                    MatchLink(
                        item_id=scored[i].item_id,
                        tc_id=test_cases[j].tc_id,
                        score=score,
                        confidence="LOW",
                        tier="adjudication",
                    )
                )

        covered = {ln.item_id for ln in links}
        mapped_tcs = {ln.tc_id for ln in links}
        counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
        for ln in links:
            counts[ln.confidence] = counts.get(ln.confidence, 0) + 1

        cov.ran = True
        cov.total_items = len(items)
        cov.presented_items = len(scored)
        cov.total_cases = len(test_cases)
        cov.links = links
        cov.covered_item_ids = [it.item_id for it in scored if it.item_id in covered]
        cov.gap_item_ids = [it.item_id for it in scored if it.item_id not in covered]
        cov.not_presented_item_ids = not_presented
        cov.orphan_tc_ids = [
            tc.tc_id for tc in test_cases if tc.tc_id not in mapped_tcs
        ]
        cov.confidence_counts = counts
        denom = cov.presented_items
        cov.coverage_pct = 100.0 * len(cov.covered_item_ids) / denom if denom else 0.0
        cov.gap_rate = 100.0 * len(cov.gap_item_ids) / denom if denom else 0.0
        cov.orphan_rate = (
            100.0 * len(cov.orphan_tc_ids) / cov.total_cases if cov.total_cases else 0.0
        )
        cov.tier_used = tier
        cov.degraded = degraded
        logger.info(
            "match_checklist: %d/%d presented requirements traced (%s tier), "
            "%d gap(s), %d not presented, %d orphan(s)",
            len(cov.covered_item_ids),
            cov.presented_items,
            tier,
            len(cov.gap_item_ids),
            len(cov.not_presented_item_ids),
            len(cov.orphan_tc_ids),
        )
        return cov
    except Exception:
        logger.exception("match_checklist failed — reporting no coverage data")
        return ChecklistCoverage()


def uncovered_items(coverage: ChecklistCoverage, items: list) -> list:
    """The ChecklistItems still reported as gaps. Never raises.

    Excludes NOT-PRESENTED items by construction: ``gap_item_ids`` only ever
    contains ids that were actually shown to the generator."""
    try:
        if not coverage or not coverage.ran:
            return []
        gaps = set(coverage.gap_item_ids or [])
        return [it for it in items if it.item_id in gaps]
    except Exception:
        logger.exception("uncovered_items failed — returning an empty list")
        return []


def coverage_to_dict(coverage: ChecklistCoverage) -> dict:
    """Plain-dict form for XLSX rows + suite_store persistence. Never raises."""
    try:
        if not coverage or not coverage.ran:
            return {}
        return {
            "total_items": coverage.total_items,
            "presented_items": coverage.presented_items,
            "total_cases": coverage.total_cases,
            "links": [
                {
                    "item_id": ln.item_id,
                    "tc_id": ln.tc_id,
                    "score": ln.score,
                    "confidence": ln.confidence,
                    "tier": ln.tier,
                }
                for ln in coverage.links
            ],
            "covered_item_ids": list(coverage.covered_item_ids),
            "gap_item_ids": list(coverage.gap_item_ids),
            "not_presented_item_ids": list(coverage.not_presented_item_ids),
            "orphan_tc_ids": list(coverage.orphan_tc_ids),
            "confidence_counts": dict(coverage.confidence_counts),
            # A degraded (lexical) run publishes NO percentage ANYWHERE: not in
            # the tally, not in the XLSX, and not in the persisted payload
            # either — otherwise a later reader of the checklists table could
            # republish the number this report deliberately refuses to print.
            "coverage_pct": None if coverage.degraded else coverage.coverage_pct,
            "gap_rate": None if coverage.degraded else coverage.gap_rate,
            "orphan_rate": None if coverage.degraded else coverage.orphan_rate,
            "tier_used": coverage.tier_used,
            "degraded": coverage.degraded,
            "notes": list(coverage.notes),
        }
    except Exception:
        logger.exception("coverage_to_dict failed — returning an empty dict")
        return {}


def checklist_tally_line(coverage: ChecklistCoverage) -> str:
    """The one-line bidirectional tally. "" when the matcher didn't run.

    DEGRADED (lexical) RUNS PUBLISH NO PERCENTAGE. TF-IDF cosine between an EARS
    requirement and a test payload rarely clears _LEXICAL_HIGH for a genuine
    paraphrase, so a correctly-covered suite would read as "3/44 (7%)". A bold
    number with a caveat underneath is still read as a number, so the number is
    removed and the line is stamped UNRELIABLE instead."""
    try:
        if not coverage or not coverage.ran or not coverage.total_items:
            return ""
        counts = coverage.confidence_counts or {}
        mapped = coverage.total_cases - len(coverage.orphan_tc_ids)
        tests = (
            f"Tests: {coverage.total_cases} ({mapped} mapped, "
            f"{len(coverage.orphan_tc_ids)} orphan(s)); "
        )
        confidence = (
            f"Confidence: {counts.get('HIGH', 0)} HIGH, "
            f"{counts.get('MEDIUM', 0)} MEDIUM, {counts.get('LOW', 0)} LOW; "
            "Mutation effectiveness: UNKNOWN."
        )
        not_presented = len(coverage.not_presented_item_ids or [])
        if not coverage.presented_items:
            # Nothing was shown to the generator, so there is no coverage to
            # measure. "0/0 (0%)" would read as a catastrophic result when the
            # real problem is a configuration one.
            return (
                "Coverage: NOT MEASURED — none of the "
                f"{coverage.total_items} requirement(s) fitted into the generator "
                "prompt, so nothing could be scored (raise "
                f"QA_CHECKLIST_MAX_PROMPT_CHARS). {tests}{confidence}"
            )
        suffix = ""
        if not_presented:
            suffix = (
                f" [{not_presented} of {coverage.total_items} requirement(s) were "
                "NOT PRESENTED to the generator and are excluded from this "
                "figure.]"
            )
        if coverage.degraded:
            return (
                "UNRELIABLE (lexical fallback — no embeddings backend): coverage "
                "percentage SUPPRESSED. "
                f"{len(coverage.covered_item_ids)} of {coverage.presented_items} "
                "requirement(s) matched lexically, which UNDERSTATES real "
                f"coverage and is not a coverage figure; {tests}{confidence}"
                f"{suffix}"
            )
        return (
            f"Coverage: {len(coverage.covered_item_ids)}/{coverage.presented_items} "
            f"requirements traced ({coverage.coverage_pct:.0f}%, "
            f"{len(coverage.gap_item_ids)} gap(s)); "
            f"{tests}{confidence}{suffix}"
        )
    except Exception:
        logger.exception("checklist_tally_line failed — returning empty string")
        return ""


def checklist_oneline(coverage: ChecklistCoverage) -> str:
    """Compact summary line for the deferred (MCP) path. Never raises."""
    try:
        line = checklist_tally_line(coverage)
        return f"\n\n**Requirements checklist:** {line}" if line else ""
    except Exception:
        logger.exception("checklist_oneline failed — returning empty string")
        return ""


def render_checklist_section(coverage: ChecklistCoverage, items: list) -> str:
    """Full markdown coverage report: tally, NOT PRESENTED items, NOT COVERED
    gaps, REVIEW_REQUIRED orphans, provenance caveats (comment-derived
    requirements are called out HERE, not only in the spreadsheet), and the
    mandatory honesty boundary. "" when the matcher did not run. Never raises."""
    try:
        if not coverage or not coverage.ran or not items:
            return ""
        by_id = {it.item_id: it for it in items}

        def _label(item_id: str) -> str:
            it = by_id.get(item_id)
            if not it:
                return ""
            source = getattr(it, "source", "") or "unattributed"
            return f"{_item_text(it)} _[source: {source}]_"

        lines = [
            "\n\n---\n\n## Requirements Checklist Coverage (bidirectional)",
            "",
            f"**{checklist_tally_line(coverage)}**",
            "",
            f"_Matcher tier: {coverage.tier_used}._",
            "",
            "_This is a SECOND, independent coverage view; it does not replace "
            "the Requirements Traceability Matrix above, and the two figures are "
            "not expected to agree. The RTM counts ACCEPTANCE CRITERIA that the "
            "test cases tagged themselves against (self-reported, one row per "
            "AC). This section counts ATOMIC REQUIREMENTS matched EXTERNALLY "
            "from each case's expected results, ignoring those tags — a "
            "different denominator, computed a different way._",
        ]
        for note in coverage.notes or []:
            lines += ["", f"> {note}"]
        for caveat in provenance_caveats(items):
            lines += ["", f"> {caveat}"]

        not_presented = coverage.not_presented_item_ids or []
        if not_presented:
            lines += [
                "",
                "### NOT PRESENTED TO GENERATOR (excluded from the coverage figure)",
                "",
                "These requirements did not fit inside the prompt budget, so the "
                "generator never saw them. They are NOT counted as gaps — this is "
                "a configuration issue, not a coverage result:",
                "",
            ]
            for nid in not_presented[:50]:
                lines.append(f"- **NOT PRESENTED: {nid}** — {_label(nid)}")
            if len(not_presented) > 50:
                lines.append(f"- … and {len(not_presented) - 50} more")

        gaps = coverage.gap_item_ids or []
        if gaps:
            lines += ["", "### NOT COVERED (forward gaps)", ""]
            for gid in gaps[:50]:
                lines.append(
                    f"- **NOT COVERED: {gid}** — {_label(gid)} "
                    "(no test case matched this requirement above the "
                    "configured threshold)"
                )
            if len(gaps) > 50:
                lines.append(f"- … and {len(gaps) - 50} more")

        orphans = coverage.orphan_tc_ids or []
        if orphans:
            lines += ["", "### REVIEW_REQUIRED (backward orphans)", ""]
            lines.append(
                "These test cases matched no checklist requirement. That is EITHER "
                "undocumented behaviour being tested OR a sign the checklist is "
                "under-decomposed — the matcher cannot tell which. Nothing was "
                "dropped:"
            )
            lines.append(
                "- " + ", ".join(orphans[:30]) + (" …" if len(orphans) > 30 else "")
            )

        low = (coverage.confidence_counts or {}).get("LOW", 0)
        if low:
            lines += [
                "",
                f"> {low} match(es) were resolved by LLM adjudication only "
                "(LOW confidence) and require human review.",
            ]

        lines += ["", HONESTY_BOUNDARY]
        return "\n".join(lines)
    except Exception:
        logger.exception("render_checklist_section failed — returning empty string")
        return ""
