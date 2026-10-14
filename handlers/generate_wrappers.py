"""Thin wrappers over the agents.host_gen_* helpers; they keep the old handler names."""

from __future__ import annotations

import sys
from typing import Any, Callable

from agents.host_gen_args import (
    CarryArgs,
    CorpusJob,
    FinishCtx,
    GateProbe,
    GenRequest,
    HintArgs,
    ImageNoteArgs,
    InputArgs,
    NoticeArgs,
    NudgeAudit,
)
from agents.host_gen_corpus_a import duplicate_case_note, recent_suite_warning_note
from agents.host_gen_corpus_b import persist_suite_to_corpus
from agents.host_gen_image_audit import audit_image_plan_nudge
from agents.host_gen_image_hint import image_gate_would_fire, pending_image_gate_hint
from agents.host_gen_image_notes import ticket_image_note
from agents.host_gen_issue_notes import attested_image_count, prepare_input_refusal
from agents.host_gen_notes import host_mode_server_llm_notice
from agents.host_gen_pipeline_a import run_prepare_generation, ticket_recency_notes
from agents.host_gen_pipeline_c import finish_prepare
from agents.host_gen_preflight import merge_carry_forward
from agents.host_gen_types import (
    PreparePayloadResult,
    _Grounding,
    _PrepRun,
    _TicketNotes,
)
from handlers import _common
from handlers.generate_host import host
from handlers.seam_alias import alias


# v1.97.0 cursor-hardening (item 3a/8): duplicate-content and recent-suite
# advisories (WARN-only) live in agents/host_gen_corpus_a; the corpus write in
# agents/host_gen_corpus_b. These wrappers keep the old names and signatures.
async def gen_duplicate_case_note(cases: list, source_url: str = "") -> tuple:
    """(note, counts) -- WARN-only duplicate check; see host_gen_corpus_a."""
    return await duplicate_case_note(cases, source_url, host()._store_hooks())


async def gen_recent_suite_warning_note(source_url: str, window_s: float) -> str:
    """WARN when a suite exists for source_url within window_s; never raises."""
    return await recent_suite_warning_note(source_url, window_s, host()._store_hooks())


async def gen_persist_suite(
    suite: object, feature_text: str = "", source_url: str = ""
) -> None:
    """Write each generated test case into the RAG corpus; best-effort."""
    job = CorpusJob(suite, feature_text, source_url)
    await persist_suite_to_corpus(job, host()._store_hooks())


# --------------------------------------------------------------------------- #
# Host-mode ("boomerang") test-case generation -- ops-3d
#
# In host mode the 8-category fan-out runs in the tester's OWN chat model on any
# MCP host. handle_prepare_test_cases runs the FRONT half (fetch + preflight +
# gate + prepare_generation) and returns a grounded payload + prep_id; the BACK
# half (submit) lands in ops-3d-1b. This block is DEAD CODE behind
# QA_GENERATION_MODE (default "server") until ops-3d-3 wires routing -- nothing
# here touches the server path (it is a NEW helper, not a refactor of
# handle_generate_test_cases).
# --------------------------------------------------------------------------- #


def _host_mode_server_llm_notice(args: NoticeArgs = NoticeArgs()) -> str:
    return host_mode_server_llm_notice(args)


async def _audit_image_plan_nudge(plan: str, channel: str, missing_ids) -> None:
    """Audit + log ONE image-plan completion nudge exit. Never raises."""
    hooks = host().build_hooks()
    await audit_image_plan_nudge(NudgeAudit(plan, channel, missing_ids), hooks)


def _image_gate_would_fire(probe: GateProbe) -> bool:
    """THE condition for the Jira image gate; see host_gen_image_hint."""
    return image_gate_would_fire(probe, host()._image_hooks())


def _pending_image_gate_hint(args: HintArgs) -> str:
    """Heads-up that the Jira image gate is STILL AHEAD; see host_gen_image_hint."""
    return pending_image_gate_hint(args, host().build_hooks())


def _prepare_input_refusal(
    text: str, jira_content_json: str, stage_token: str
) -> tuple[str, str]:
    """``(refusal, jira_content_json)`` for the input-only refusals of a prepare."""
    args = InputArgs(text, jira_content_json, stage_token)
    return prepare_input_refusal(args, host()._jira_hooks())


def _attested_image_count(attached_image_count: Any, attached_images: Any) -> int:
    """This call's ATTESTED screens; see host_gen_issue_notes."""
    hooks = host()._image_hooks()
    return attested_image_count(attached_image_count, attached_images, hooks)


def _merge_carry_forward(args: CarryArgs) -> str:
    """Fold the carry-forward verdict on *prep* into *carry*; see host_gen_preflight."""
    return merge_carry_forward(args, host()._capture_hooks())


def _ticket_image_note(url_content: dict, attested: int, captured: int) -> str:
    """What the reply must say about the ticket's own screens, or ""."""
    args = ImageNoteArgs(url_content, attested, captured)
    return ticket_image_note(args, host()._image_hooks())


async def _ticket_recency_notes(text: str, grounded: _Grounding) -> _TicketNotes:
    """The ticket's stale-snapshot, recent-suite and condensed-payload notes."""
    return await ticket_recency_notes(text, grounded, host().build_hooks())


async def _run_prepare_generation(
    req: GenRequest, progress: _common.ProgressCb
) -> PreparePayloadResult | Any:
    """Run prepare_generation; an early-return tuple becomes an audited clarify."""
    return await run_prepare_generation(req, progress, host().build_hooks())


async def _finish_prepare(
    run: _PrepRun, missing_note: Callable[[], str]
) -> PreparePayloadResult:
    """Stage the envelope, build the payload, audit, and ship the reply."""
    return await finish_prepare(FinishCtx(run, missing_note, host().build_hooks()))


_duplicate_case_note = alias(sys.modules[__name__], "gen_duplicate_case_note")
_persist_suite_to_corpus = alias(sys.modules[__name__], "gen_persist_suite")
_recent_suite_warning_note = alias(
    sys.modules[__name__], "gen_recent_suite_warning_note"
)
