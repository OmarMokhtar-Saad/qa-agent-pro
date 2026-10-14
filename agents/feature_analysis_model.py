"""FeatureAnalysisReport model (leaf of feature_analysis)."""

from __future__ import annotations

from pydantic import BaseModel, Field

# The two plain-string fields; every other field of FeatureAnalysisReport is a
# list[str]. Used by finalize_feature_report to coerce an UNTRUSTED submission.
_STR_FIELDS = ("feature_summary", "business_objective")


class FeatureAnalysisReport(BaseModel):
    """Structured enterprise feature-analysis report merged from Jira + screenshots."""

    model_config = {"extra": "forbid"}

    feature_summary: str = ""
    business_objective: str = ""
    functional_requirements: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    preconditions: list[str] = Field(default_factory=list)
    user_roles: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    validation_rules: list[str] = Field(default_factory=list)
    error_handling: list[str] = Field(default_factory=list)
    edge_cases: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    missing_requirements: list[str] = Field(default_factory=list)
    ui_analysis: list[str] = Field(default_factory=list)
    user_flow: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
