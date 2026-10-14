"""Host-mode prepare, image gate beat 2: the plan-completion nudge and the informed ask."""

from __future__ import annotations

import sys

from agents.host_gen_types import (
    PreparePayloadResult,
    _GateCtx,
    _Grounding,
    _TextOnlySkip,
)
from handlers import _common, capture, image_gate
from handlers.generate_args import Beat2Args, PlanNudgeArgs
from handlers.generate_host import host
from handlers.seam_alias import alias


# Plan-completion nudge: a plan that PROMISED images and has not
# delivered them yet gets ONE actionable instruction per missing channel,
# on every call until it is satisfied. `image_gate_ack=true` is always
# the way out, so this can never dead-end. Still no fetch, no prep.
#
# Attach nudge:
# I1 (2026-08-10): this exit is tester-visible and used to leave no
# trace at all, so a loop of them was invisible in telemetry.
#
# Capture nudge:
# I1 (2026-08-10): same finding as the attach nudge above. The
# unresolved-id count is the useful signal here -- it separates
# "never captured anything" from "sent ids that expired".
async def image_plan_nudge(args: PlanNudgeArgs) -> str | None:
    """The audited nudge for the first channel the plan has not delivered, or None."""
    plan, ctx = args.plan, args.ctx
    if plan in ("jira_attach", "jira_both") and not ctx.attested:
        await host()._audit_image_plan_nudge(plan, "attach", args.cap_missing)
        return host()._attach_nudge_text(plan)
    if plan in ("jira_device", "jira_both", "device") and not args.cap_images:
        await host()._audit_image_plan_nudge(plan, "capture", args.cap_missing)
        return host()._capture_nudge_text(plan)
    return None


def _evidence(grounded: _Grounding, ctx: _GateCtx, plan: str) -> tuple:
    """``(beat2, kind, count, names, have_images, skip_count, skip_names)``."""
    n, names, kind = image_gate._ticket_image_evidence(grounded.url_content)
    chat_side = max(0, len(ctx.attached_images or []) - ctx.captured)
    have = max(chat_side, ctx.attested) + ctx.captured
    beat2 = image_gate.image_gate_second_beat(
        image_gate._BeatTwoReq(
            n_images=n, names=names, kind=kind, plan=plan, have_images=have
        )
    )
    skip_n, skip_names = image_gate._text_only_image_skip_stamp(plan, n, names)
    return beat2, kind, n, names, have, skip_n, skip_names


# --- Jira image gate, BEAT 2: the INFORMED ask -------------------- #
# The ticket is in hand now, so this one can NAME the screens. Fires
# ONLY when the ticket revealed images and nothing supplied them;
# SILENT otherwise, so a plan that already covered the screens is never
# asked twice. Runs BEFORE prepare_generation, so a gated round costs
# no enrichment, no prep row and no generation -- and because no prep is
# saved, the duplicate-prep guard cannot fire on the follow-up call.
# The captures are still in the tray (peeked, not popped), so re-sending
# the same capture_ids works.
# P0-3: runs whatever `image_gate_ack` says; the ack now only opens
# the tester confirmation dialog below.
#
# N4 (2026-08-10): bound, not inlined -- the audit row could not tell
# "no screens at all" from "3 of 4 arrived" without the ratio.
# 2026-08-31 (C2): NOT a sum. `attached_images` already contains the
# device captures (merged above) and, on hosts that forward them, the
# SAME chat screens `_attested` counts -- so summing double-counted
# one channel and let 2-of-3 read as 3-of-3, re-opening the exact
# subset-generation hole the completeness rule was added to close.
# Chat bytes vs chat attestation: take the larger, they are one
# channel. Device captures are a genuinely separate channel and add.
# RESIDUAL, accepted: a host that folds its device captures INTO
# attached_image_count over-counts by the overlap. Unfixable here --
# the count is an unverifiable host claim either way (see the
# attested_without_bytes audit stamp below) -- and it is a much
# narrower hole than the one it replaces, which needed no
# misreporting at all to fire.
#
# v1.97.0 cursor-hardening (item 2): text-only source_plan='jira'
# clears beat 2 in ONE ask now (see image_gate_second_beat above),
# but the skip must still be disclosed, never silent -- stamped into
# envelope["meta"] below and rendered at submit time by
# text_only_image_skip_note.
#
# P0-3: the ack is the AGENT's claim that the tester agreed; only
# the tester's own pick in this dialog clears beat 2. No dialog,
# a dismissal or the attach option keep it shut.
#
# K2 (2026-08-10): INSIDE the gated branch on purpose. At the
# _ticket_image_evidence call above, _beat2 is not decided yet, so
# shelving there would stamp labels even when beat 2 stays SILENT
# (images already supplied) and a later unrelated capture would pop
# them. This branch is the only path that actually sends the tester
# off to capture.
# N3b's fetch-failure disclosure went with the fetch (batch D1)
# and its dead renderer (2026-09-02); a revived fetch adds its
# own note here.
async def _image_gate_beat2(args: Beat2Args) -> PreparePayloadResult | _TextOnlySkip:
    """Jira image gate beat 2: the informed ask, or the text-only skip stamp."""
    ctx, plan = args.ctx, args.plan
    beat2, kind, n, names, have, skip_n, skip_names = _evidence(
        args.grounded, ctx, plan
    )
    if beat2 and args.image_gate_ack:
        beat2 = await image_gate._confirm_image_gate_skip(args.choose, beat2)
    if beat2:
        capture._shelve_ticket_image_labels(names, ctx.text)
        await _common.audit(
            "mcp_image_gate_beat2",
            detail={"kind": kind, "count": n, "plan": plan, "have_images": have},
        )
        return PreparePayloadResult(clarify=beat2)
    return _TextOnlySkip(skip_n, skip_names)


_image_plan_nudge = alias(sys.modules[__name__], "image_plan_nudge")
