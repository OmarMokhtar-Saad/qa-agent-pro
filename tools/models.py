from __future__ import annotations

import hashlib
import logging
import re
import uuid
from enum import Enum
from typing import Literal, Optional

from pydantic import (
    BaseModel,
    Field,
    PrivateAttr,
    field_validator,
    model_validator,
)

logger = logging.getLogger(__name__)


def _compute_stable_id(title: str, steps: list["TestStep"]) -> str:
    """Deterministic content hash of a test case's title + ordered steps.

    Unlike the display-oriented ``tc_id`` (which is reassigned TC-001..N in final
    risk order on every generation), the stable id is derived purely from the
    case's semantic content, so the same test case keeps the same id across
    regenerations, exports and persisted runs (QW-13 / I-025). It is what
    downstream persistence (T-01), TMS push (T-10) and dedup key off.
    """
    payload = title.strip() + "␟".join(
        f"␞{s.action.strip()}␞{(s.test_data or '').strip()}␞{s.expected_result.strip()}"
        for s in steps
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"SID-{digest}"


class Priority(str, Enum):
    CRITICAL = "Critical"
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class TestType(str, Enum):
    FUNCTIONAL = "Functional"
    REGRESSION = "Regression"
    SMOKE = "Smoke"
    INTEGRATION = "Integration"
    EXPLORATORY = "Exploratory"
    ACCESSIBILITY = "Accessibility"
    PERFORMANCE = "Performance"
    SECURITY = "Security"
    BOUNDARY = "Boundary"
    NEGATIVE = "Negative"


class AutomationStatus(str, Enum):
    AUTOMATED = "Automated"
    MANUAL = "Manual"
    TO_BE_AUTOMATED = "To Be Automated"
    CANNOT_BE_AUTOMATED = "Cannot Be Automated"
    NOT_APPLICABLE = "Not Applicable"


class TestStep(BaseModel):
    model_config = {"extra": "forbid"}

    step_number: int = Field(ge=1, description="Sequential step number starting at 1")
    action: str = Field(min_length=5, description="What the tester should do")
    test_data: Optional[str] = Field(
        None, description="Concrete test data, e.g. 'email: test@example.com'"
    )
    expected_result: str = Field(
        min_length=5, description="Observable, verifiable outcome"
    )

    @field_validator("action", "expected_result", mode="after")
    @classmethod
    def strip_and_check_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Field cannot be empty or whitespace only")
        return v


class TestDataItem(BaseModel):
    """One field's data-provisioning plan for a test case.

    Declares WHAT data a case needs and HOW a manual tester should source it, so
    testers stop guessing which values must be unique per run, come from a seeded
    account, or chain from an earlier case. ``example_value`` MUST be an obviously
    fake placeholder — never real-looking PII.

    An unknown ``strategy`` deliberately raises ValidationError (mirroring Priority /
    TestType) so the per-category retry regenerates it — there is no lenient-coercion
    precedent in this module.
    """

    model_config = {"extra": "forbid"}

    field: str = Field(
        min_length=1, max_length=80, description="Data field name, e.g. 'username'"
    )
    strategy: Literal["unique_per_run", "seed_account", "chained", "static"] = Field(
        description="How the value is sourced: unique_per_run / seed_account / "
        "chained / static"
    )
    example_value: str = Field(
        default="",
        max_length=200,
        description="A SAFE, obviously-fake example value — never real-looking PII",
    )
    chained_from: Optional[str] = Field(
        default=None,
        description="tc_id of the earlier case that produces this value "
        "(only when strategy == 'chained')",
    )
    notes: str = Field(
        default="",
        max_length=200,
        description="Short hint on how to obtain/rotate the value",
    )


def format_test_data_lines(items: list["TestDataItem"]) -> list[str]:
    """Render a case's test_data plan into compact one-per-field display lines.

    Pure and never-raises; returns [] for an empty/None plan so callers that only
    render when a case HAS test_data stay byte-identical to the pre-feature output.
    """
    lines: list[str] = []
    try:
        for it in items or []:
            chain = (
                f" (from {it.chained_from})"
                if it.strategy == "chained" and it.chained_from
                else ""
            )
            example = f": {it.example_value}" if it.example_value else ""
            note = f" — {it.notes}" if it.notes else ""
            lines.append(f"{it.field} [{it.strategy}]{chain}{example}{note}")
    except Exception:
        return lines
    return lines


# --------------------------------------------------------------------------- #
# Requirement-id helpers
#
# They live HERE, beside TestCase, rather than in tools/rtm.py where
# normalize_ac_id was born, because the five exporters need them on every row of
# every export and tools/rtm.py is a comparatively heavy sibling -- it pulls in
# tools.atomic_checklist and tools.embeddings, which a plain CSV / feature file /
# Playwright skeleton has no reason to load. tools.rtm RE-EXPORTS
# normalize_ac_id, so every existing `from tools.rtm import normalize_ac_id`
# keeps resolving and no caller changed. Both are pure and never raise.
# --------------------------------------------------------------------------- #

_CANONICAL_AC_RE = re.compile(r"^AC-\d{3,}$")


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


def display_requirement_id(raw: str | None) -> str:
    """A case's requirement tag as a reader should see it, or "" when it has none.

    F06 (2026-08-19). Canonicalises an AC-SHAPED tag through ``normalize_ac_id``
    so the exported Requirement ID column and the Requirements Traceability sheet
    cannot name one criterion two ways ("AC-1" in the column, "AC-001" in the
    sheet, matched as the same thing).

    Anything NOT AC-shaped is returned stripped but otherwise UNTOUCHED:
    ``normalize_ac_id`` upper-cases and de-spaces every value it does not
    recognise, which is right for something only ever COMPARED and wrong for
    something rendered into a deliverable -- "Login must work" is not
    "LOGINMUSTWORK". Never raises."""
    try:
        text = str(raw or "").strip()
        if not text:
            return ""
        canonical = normalize_ac_id(text)
        return canonical if _CANONICAL_AC_RE.match(canonical) else text
    except Exception:  # pragma: no cover - a display helper must never raise
        logger.debug("display_requirement_id failed -- returning empty", exc_info=True)
        return ""


class TestCase(BaseModel):
    model_config = {"extra": "forbid"}

    tc_id: str = Field(
        pattern=r"^TC-\d{3,6}$",
        description="Unique identifier, format TC-001 through TC-999999, sequential from TC-001",
    )
    module: str = Field(
        min_length=2, max_length=100, description="Feature or module name"
    )
    title: str = Field(
        min_length=10, max_length=250, description="Concise test case title"
    )
    priority: Priority = Field(
        description="Exactly one of: Critical, High, Medium, Low"
    )
    type: TestType = Field(
        description="Exactly one of: Functional, Regression, Smoke, Integration, "
        "Exploratory, Accessibility, Performance, Security, Boundary, Negative"
    )
    preconditions: Optional[str] = Field(
        None, description="System state required before test execution"
    )
    steps: list[TestStep] = Field(
        min_length=1, description="Ordered steps numbered sequentially from 1"
    )
    postconditions: Optional[str] = Field(
        None, description="Expected system state after test execution"
    )
    automation_status: AutomationStatus = Field(
        default=AutomationStatus.MANUAL,
        description="Exactly one of: Automated, Manual, To Be Automated, Cannot Be Automated, Not Applicable",
    )
    requirement_id: Optional[str] = Field(
        None, description="Linked Jira ticket or requirement ID"
    )
    risk_score: int = Field(
        default=0,
        description="Heuristic risk score (higher = riskier); 0 = not yet scored",
    )
    risk_label: str = Field(
        default="",
        description="Risk tier: CRITICAL / HIGH / MEDIUM / LOW (empty = not yet scored)",
    )
    risk_rationale: str = Field(
        default="", description="Human-readable explanation of the risk score"
    )
    stable_id: str = Field(
        default="",
        description="Auto-generated content hash — LEAVE EMPTY. The system derives "
        "it from the title and steps so the case keeps a stable identity across "
        "regenerations and exports, independent of the display-order tc_id.",
    )
    category: Optional[str] = Field(
        default=None,
        max_length=60,
        description="Which of the 8 generation categories produced this case "
        "(Positive / Happy Path, Negative / Error Flows, Boundary Values, Edge "
        "Cases, State Transitions, Security, UI/UX Validation, Integration). "
        "Bounded free text: normalised at the untrusted boundary, empty when it "
        "could not be resolved -- never guessed.",
    )
    category_source: Optional[Literal["server", "host"]] = Field(
        default=None,
        description='Where `category` came from. "server": derived by the server (the fan-out category, or the category_name argument of a qa_submit_category call). "host": self-reported by the tester\'s chat model on a single merged submission, where the server has no grouping of its own. Set in code on every path -- a model-supplied value is always overwritten -- so a re-export can still tell the two apart.',
    )
    test_data: list[TestDataItem] = Field(
        default_factory=list,
        description="Optional per-case data-provisioning plan: for each data field "
        "the test needs, how to source its value (unique per run / seed account / "
        "chained from an earlier case / static) with a SAFE fake example. Empty "
        "for a case that manipulates no data.",
    )

    @field_validator("steps", mode="after")
    @classmethod
    def validate_step_numbering(cls, steps: list[TestStep]) -> list[TestStep]:
        for i, step in enumerate(steps, start=1):
            if step.step_number != i:
                raise ValueError(
                    f"Step numbering must be sequential: expected {i}, got {step.step_number}"
                )
        return steps

    @model_validator(mode="after")
    def _assign_stable_id(self) -> "TestCase":
        # Always (re)derive the stable id from content so it is deterministic and
        # cannot be spoofed by an LLM/attacker supplying its own value. Recomputing
        # on a round-tripped case yields the same id (content is unchanged).
        self.stable_id = _compute_stable_id(self.title, self.steps)
        return self


class TestSuite(BaseModel):
    model_config = {"extra": "forbid"}

    suite_id: str = Field(
        default_factory=lambda: uuid.uuid4().hex,
        description="Auto-generated suite identifier — LEAVE EMPTY. Assigned by the "
        "system so the suite can be persisted, re-exported and pushed to a TMS by "
        "a stable key.",
    )
    test_cases: list[TestCase] = Field(
        min_length=1, description="All generated test cases"
    )

    # ``_report_artifacts`` (AC-Validation / Test Plan) was DELETED 2026-08-30
    # with tools/test_plan_report.py and the two XLSX report sheets: P2-H had
    # already removed its only writer on 2026-08-16, so it was permanently None.
    # The PrivateAttrs below are the surviving members of the same family and
    # carried their rationale by reference to it, which is why each now states
    # that rationale itself.

    # Atomic Requirements Checklist + its coverage audit (Batch 2), attached
    # post-generation by agents.test_scenario_agent whenever a checklist was
    # produced -- unconditional since 2026-08-14, when
    # QA_ATOMIC_CHECKLIST_ENABLED was deleted and the behaviour hardcoded ON.
    # A PrivateAttr so it is EXCLUDED from the JSON schema used as an LLM
    # response_model (it must never pollute generation) and from serialization.
    # tools.xlsx_generator and tools.mcp_handlers read it via getattr.
    _checklist_artifacts: Optional[dict] = PrivateAttr(default=None)

    # {tc_id: note} for the XLSX Notes column — the Batch 3
    # standing-rules pack's mechanical [ASSUMED] /
    # [NEEDS-CLARIFICATION] label, attached post-renumber by
    # agents.test_scenario_agent. A PrivateAttr so it is
    # EXCLUDED from the JSON schema used as an LLM
    # response_model and from serialization.
    # tools.xlsx_generator reads it via getattr.
    _rule_pack_notes: Optional[dict] = PrivateAttr(default=None)

    # Rows for the "Assumed Requirements" sheet: the cases an entailment review
    # judged ungrounded, moved OFF the executable suite rather than deleted
    # (host_mode.grounding_review_enabled, hardcoded OFF 2026-08-13). A
    # PrivateAttr for the same reasons as
    # the two above -- it must not appear in the JSON schema handed to a model.
    _assumed_artifacts: Optional[dict] = PrivateAttr(default=None)

    # Rows for the "Generation Notes" sheet: what this run could NOT do, or did
    # short of what was asked -- an under-generated category, an absent
    # requirements checklist, a preflight that left no evidence it ran. A list
    # of (finding, detail) pairs, attached by tools.mcp_handlers just before the
    # export.
    #
    # WHY IT EXISTS (2026-08-15, F5/F6/F7): each of these was already disclosed
    # in the tool reply, and each was measured surviving into a finalized suite
    # anyway -- because the reply is transient chat that a summarising host
    # model prunes, while the .xlsx is the artifact the tester keeps, attaches
    # to a ticket and reads a week later. A caveat that does not travel with the
    # deliverable is not a caveat. A PrivateAttr for the same reasons as the
    # attributes above: it must never appear in the JSON schema handed to a
    # model, and must not be serialized.
    _generation_notes: Optional[list] = PrivateAttr(default=None)

    # Rows for the "Requirements Traceability" sheet: one per acceptance
    # criterion, with the case COUNT and the linked tc_ids. Attached by
    # agents.test_scenario_agent._finalize_generation -- the one place holding
    # both the final renumbered cases and the ACs.
    #
    # WHY IT EXISTS (F06, 2026-08-19): the finalize reply claims "N/N acceptance
    # criteria traced", and until this sheet existed that claim could only be
    # checked against data/suites.db -- `requirement_id` was on every case and on
    # no exported column. A PrivateAttr for the same reasons as the attributes
    # above: it must never appear in the JSON schema handed to a model, and must
    # not be serialized.
    _rtm_artifacts: Optional[dict] = PrivateAttr(default=None)

    @field_validator("test_cases", mode="after")
    @classmethod
    def validate_unique_ids(cls, cases: list[TestCase]) -> list[TestCase]:
        ids = [tc.tc_id for tc in cases]
        if len(ids) != len(set(ids)):
            seen: set[str] = set()
            dupes = [x for x in ids if x in seen or seen.add(x)]  # type: ignore[func-returns-value]
            raise ValueError(f"Duplicate TC IDs found: {dupes}")
        return cases
