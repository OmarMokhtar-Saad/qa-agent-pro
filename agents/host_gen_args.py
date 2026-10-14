"""Host mode: argument bundles for the prepare pipeline (C3c).

Each NamedTuple holds exactly the positional parameters of the handler function
it replaces, so a moved function takes ``(args, hooks)`` and stays within three
parameters. Field order matches the old signatures.
"""

from __future__ import annotations

from typing import Any, Callable, NamedTuple

from agents.host_gen_hooks import GenerateHooks
from agents.host_gen_types import _Grounding, _PrepRun


class CarryAsk(NamedTuple):
    prep: dict | None
    image_carry_ack: bool
    capture_ids: list | None = None
    have_attested: int = 0


class CorpusJob(NamedTuple):
    suite: object
    feature_text: str = ""
    source_url: str = ""


class NudgeAudit(NamedTuple):
    plan: str
    channel: str
    missing_ids: Any


class GateProbe(NamedTuple):
    text: str
    plan: str
    jira_content_json: str
    attached_images: object
    attested: object


class HintArgs(NamedTuple):
    text: str
    source_plan: str
    jira_content_json: str
    attached_images: Any
    attached_image_count: Any
    capture_ids: Any


class CarryArgs(NamedTuple):
    prep: dict
    carry: Any
    image_carry_ack: bool
    have_attested: int


class InputArgs(NamedTuple):
    text: str
    jira_content_json: str
    stage_token: str


class ImageNoteArgs(NamedTuple):
    url_content: dict
    attested: int
    captured: int


class GenRequest(NamedTuple):
    text: str
    grounded: _Grounding
    attached_images: list | None
    capture_ids: list | None


class FinishCtx(NamedTuple):
    run: _PrepRun
    missing_note: Callable[[], str]
    hooks: GenerateHooks


class NoticeArgs(NamedTuple):
    ac_boomeranged: bool = False
    img_boomeranged: bool = False
    checklist_boomeranged: bool = False
    rule_packs_narrowed: bool = False


class CondensedWarnArgs(NamedTuple):
    chars: int
    updated: str
    prior_chars: int
    when: str
