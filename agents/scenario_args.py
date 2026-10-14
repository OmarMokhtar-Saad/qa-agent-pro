"""Parameter objects of the chat-only test-scenario pipeline (types only)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from tools.rtm import AcceptanceCriterion
    from tools.rule_packs import RulePackResult


@dataclass(frozen=True)
class GenerationRequest:
    """Everything ``_prepare_generation`` takes, in the old parameter order."""

    feature_text: str
    url_content: dict | None = None
    ui_content: dict | None = None
    attached_images: list[dict] | None = None
    spec_text: str | None = None
    openapi_text: str | None = None
    single_screen: bool = False
    on_status: Callable[[str], Awaitable[None]] | None = None


@dataclass(frozen=True)
class GroundingSource:
    """The source text a ``grounding_sections`` call is grounded against."""

    source_text: str | None = ""
    user_msg: str = ""
    ac_texts: list[str] | None = None


class RtmFacts(NamedTuple):
    """The ACs, rule packs and host flag that shape the RTM hint."""

    acs: list[AcceptanceCriterion]
    rule_packs: RulePackResult
    host_ac_job: bool


class RulePackInputs(NamedTuple):
    """The page evidence the rule-pack builder reads."""

    jira_context_text: str
    ui_content: dict | None
    openapi_text: str | None
    images_present: bool
