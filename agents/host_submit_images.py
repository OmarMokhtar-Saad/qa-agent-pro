"""Image stage of qa_submit_suite (moved from handlers, C3a step 2c).

The image job's return field. This server described NOTHING itself on
this prep -- no ask_vision at all -- so the host's own multimodal model
is the only thing that read the screenshots. UNTRUSTED (pixels can be
attacker-controlled just like the _GUARD-wrapped ticket text): shape
validated, URL-stripped, newline-collapsed and capped by host_mode
before anything renders it, and it feeds NO prompt and NO exporter
field. Keyed off the prep's meta stamp, so a mid-flow .env flip cannot
change an in-flight prep and a normal submit is byte-identical.

Batch 4 LAYER 2: the opt-in refusal runs after the sidecar/merge branches,
so it sees BOTH finalize routes, and before the finalize, the export and the
persist, so a refusal costs one round trip and destroys nothing (the prep is
kept, no staged row is dropped). On the staged route option 2 must ask for
the missing verdicts ALONE; the hint is computed only when rows exist.

TWO-BEAT ack, copied from the volume gate: the prep is marked refused so a
LATER image_relevance_ack is honoured, while an ack sent on the FIRST submit
-- which the tester cannot have seen this finding for -- is refused and told
so. Non-fatal: an unpersisted mark only means the next ack is refused again,
which fails in the SAFE direction.
"""

from __future__ import annotations

import logging
from typing import Any, NamedTuple

from agents.host_category_calls import StagedHintIn
from agents.host_prep_relevance_gate import RelevanceCheck
from agents.host_submit_ports_prep import SubmitPorts
from agents.host_submit_types import SubAcks, SubImages, SubPrep
from config.settings_guard import never_raise
from tools import prep_store

logger = logging.getLogger("agents.host_mode")

_FIELD_SHAPE = (
    "[one entry per image, each with `relevant` (the "
    "bare string `yes`, `no` or `unsure`) and a "
    "one-line `relevance_reason`]"
)


class ImageIn(NamedTuple):
    prep: SubPrep
    parsed: Any
    staged_rows: int
    acks: SubAcks


class GateIn(NamedTuple):
    meta: dict
    result: Any
    prep_id: str
    staged_rows: int
    ack: bool


class EffectsIn(NamedTuple):
    prep: SubPrep
    mode: str
    counts: dict


def _refused_envelope(prep: SubPrep) -> dict:
    return {
        **prep.envelope,
        "meta": {**prep.meta, "image_relevance_refused": True},
    }


async def mark_refused(prep: SubPrep) -> None:
    # pragma: no cover - never block the refusal
    with never_raise(logger, "image_relevance_refused mark failed", exc_info=True):
        _mark = await prep_store.update_prep(prep.prep_id, _refused_envelope(prep))
        if (_mark or {}).get("error"):
            logger.warning(
                "prep %s not marked image_relevance_refused (%s)",
                prep.prep_id,
                (_mark or {}).get("error"),
            )


async def _staged_hint(args: GateIn, ports: SubmitPorts) -> str:
    if not args.staged_rows:
        return ""
    return await ports.category.staged_resubmit_hint(
        StagedHintIn(
            args.prep_id,
            "image_descriptions",
            _FIELD_SHAPE,
            args.staged_rows,
        )
    )


async def gate_verdict(args: GateIn, ports: SubmitPorts) -> tuple[str, str]:
    hint = await _staged_hint(args, ports)
    check = RelevanceCheck(args.meta, args.result, args.prep_id, bool(args.ack), hint)
    imode, imd = ports.prep_gate.image_relevance_gate(check)
    return imode, imd


async def _audit_refused(args: EffectsIn, ports: SubmitPorts) -> None:
    await mark_refused(args.prep)
    await ports.common.audit(
        "mcp_submit_suite_refused",
        entity_id=args.prep.prep_id,
        detail={
            "reason": "image_relevance",
            "host_image_off_topic": int(args.counts.get("no") or 0),
            "host_image_relevance_ran": bool(args.counts.get("ran")),
        },
    )


async def _audit_override(args: EffectsIn, ports: SubmitPorts) -> None:
    await ports.common.audit(
        "mcp_submit_suite_image_override",
        entity_id=args.prep.prep_id,
        detail={
            "reason": "image_relevance_ack",
            "host_image_off_topic": int(args.counts.get("no") or 0),
        },
    )


async def gate_effects(args: EffectsIn, ports: SubmitPorts) -> None:
    if args.mode == "refuse":
        await _audit_refused(args, ports)
    if args.mode == "acked":
        await _audit_override(args, ports)


def _job_notes(args: ImageIn, ports: SubmitPorts) -> tuple[str, Any, dict]:
    """Skip note always; the job's sections and counts only when it shipped."""
    meta = args.prep.meta
    img_note = ""
    img_note += ports.image.text_only_image_skip_note(meta)
    if not meta.get("host_image_job"):
        return img_note, None, {}
    raw = getattr(args.parsed, "raw_image_descriptions", None)
    result = ports.media.extract_host_image_descriptions(
        raw, relevance=bool(meta.get("host_image_relevance"))
    )
    img_note += ports.media.build_host_image_section(result)
    img_note += ports.image.attested_image_gap_note(
        meta.get("attached_image_count"),
        result,
        captured=meta.get("captured_image_count"),
    )
    return img_note, result, ports.media.image_relevance_counts(result)


async def review(args: ImageIn, ports: SubmitPorts) -> str | SubImages:
    prep = args.prep
    img_note, result, counts = _job_notes(args, ports)
    if not prep.meta.get("host_image_job"):
        return SubImages(img_note, result, counts)
    gate_in = GateIn(
        prep.meta, result, prep.prep_id, args.staged_rows, args.acks.image_relevance
    )
    imode, imd = await gate_verdict(gate_in, ports)
    await gate_effects(EffectsIn(prep, imode, counts), ports)
    if imode == "refuse":
        return f"{prep.version_note}{img_note}{imd}"
    if imode == "acked":
        img_note += imd
    return SubImages(img_note, result, counts)
