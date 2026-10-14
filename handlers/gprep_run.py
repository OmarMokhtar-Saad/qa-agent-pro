"""The generation half of PREPARE: grounding, generation and the finished reply."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from agents.host_gen_args import GenRequest
from handlers import generate, gprep_budget
from handlers.generate_args import Beat2Args
from handlers.gprep_args import _PrepArgs, _PrepFront, _PrepParts
from handlers.gprep_pre import _host_generation_mode

logger = logging.getLogger("tools.mcp_handlers")


@dataclass(frozen=True)
class _Stage:
    """What grounding resolved, ahead of the generation call."""

    grounded: Any
    skip: Any
    notes: Any
    host_images: Any
    img: Any
    host_mode_on: bool


def _run_args_fields(f: _PrepFront) -> dict:
    """The ``_PrepRun`` fields read from the call arguments and the front half."""
    a = f.args
    return {
        "text": a.text,
        "capture_ids": a.capture_ids,
        "cap_labels": f.cap_labels,
        "image_carry_ack": a.image_carry_ack,
        "carry": f.carry,
        "intake": f.intake,
    }


def _run_parts_fields(f: _PrepFront, parts: _PrepParts) -> dict:
    """The ``_PrepRun`` fields read from the generation stage."""
    prepared = parts.prepared
    return {
        "grounded": parts.grounded,
        "prepared": prepared,
        "skip": parts.skip,
        "notes": parts.notes,
        "img": parts.img,
        "host_amb": parts.host_mode_on,
        "ac_job": bool(
            parts.host_mode_on and not prepared.source_acs and not prepared.acs
        ),
        "checklist_job": parts.checklist_job,
        "rp_narrowed": generate._rule_pack_narrowed(prepared, parts.checklist_job),
        "prospective_images": parts.prospective,
        "captured_shipped": gprep_budget._shipped_capture_count(
            parts.prospective, f.cap_images
        ),
    }


def _build_prep_run(f: _PrepFront, parts: _PrepParts) -> generate._PrepRun:
    """Assemble the ``_PrepRun`` the finishing half reads."""
    return generate._PrepRun(**_run_args_fields(f), **_run_parts_fields(f, parts))


def _missing_note_of(f: _PrepFront) -> str:
    """The note on captures that could not be read back."""
    return generate._missing_captures_note(len(f.cap_missing), f.missing_rendered)


async def _ground(a: _PrepArgs) -> str | generate._Grounding:
    """Ground and gate the inputs: the grounding or the clarify text."""
    return await generate.ground_and_gate(
        generate.GroundArgs(
            a.text,
            attached_images=a.attached_images,
            proceed_anyway=a.proceed_anyway,
            choose=a.choose,
            ask_text=a.ask_text,
            progress=a.progress,
            jira_content_json=a.jira_content_json,
        )
    )


async def _ground_stage(
    f: _PrepFront,
) -> generate.PreparePayloadResult | tuple[Any, Any, Any]:
    """Ground and gate, run beat 2: (grounded, skip, notes) or an early reply."""
    a = f.args
    grounded = await _ground(a)
    if isinstance(grounded, str):
        return generate.PreparePayloadResult(clarify=grounded)
    skip = await generate._image_gate_beat2(
        Beat2Args(grounded, f.gctx, f.plan, a.image_gate_ack, a.choose)
    )
    if isinstance(skip, generate.PreparePayloadResult):
        return skip
    notes = await generate._ticket_recency_notes(a.text, grounded)
    return grounded, skip, notes


def _stage_parts(s: _Stage, prepared: Any, checklist_job: Any) -> _PrepParts:
    """The ``_PrepParts`` of a generated stage."""
    prospective = generate._prospective_images(s.img.job, s.host_images, s.grounded)
    return _PrepParts(
        s.grounded,
        prepared,
        prospective,
        s.skip,
        s.notes,
        s.img,
        s.host_mode_on,
        checklist_job,
    )


async def _generate_stage(f: _PrepFront, s: _Stage) -> generate.PreparePayloadResult:
    """Generate, build the run and finish the reply."""
    a = f.args
    checklist_job = generate._checklist_job_on()
    prepared = await generate._run_prepare_generation(
        GenRequest(a.text, s.grounded, a.attached_images, a.capture_ids), a.progress
    )
    if isinstance(prepared, generate.PreparePayloadResult):
        return prepared
    parts = _stage_parts(s, prepared, checklist_job)
    run = _build_prep_run(f, parts)
    return await generate._finish_prepare(run, lambda: _missing_note_of(f))


async def _prepare_generate(f: _PrepFront) -> generate.PreparePayloadResult:
    """Grounding, the second image beat and generation, then the finished reply."""
    a = f.args
    host_mode_on = _host_generation_mode()
    staged = await _ground_stage(f)
    if isinstance(staged, generate.PreparePayloadResult):
        return staged
    grounded, skip, notes = staged
    host_images = generate._host_forward_images(
        grounded, a.attached_images, host_mode_on
    )
    img = generate._image_job_flags(host_mode_on, host_images, f.intake.attested)
    stage = _Stage(grounded, skip, notes, host_images, img, host_mode_on)
    return await _generate_stage(f, stage)
