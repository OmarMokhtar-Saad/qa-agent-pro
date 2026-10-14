"""Host-mode prepare, image gate beat 1: elicit the source plan or audit an attested skip."""

from __future__ import annotations

import sys

from agents.host_gen_args import GateProbe
from agents.host_gen_types import _Beat1, _GateCtx
from handlers import _common, image_gate
from handlers.generate_args import Beat1Args
from handlers.generate_host import host
from handlers.seam_alias import alias


async def _beat1_fire(args: Beat1Args) -> _Beat1:
    """The gate fires: ask for the source plan, or hand back the menu clarify."""
    picked, status = await image_gate.elicit_source_plan_status(
        args.choose, args.ask_text
    )
    await _common.audit(
        "mcp_image_gate_beat1",
        detail={"resolved": bool(picked), "plan": picked, "elicit": status},
    )
    if not picked:
        menu = image_gate._image_gate_menu_markdown(status, args.source_plan)
        return _Beat1(args.plan, menu)
    return _Beat1(picked, None)


async def _beat1_attested_skip(ctx: _GateCtx) -> None:
    """Stamp the unverifiable attested-without-bytes skip so it is never silent."""
    await _common.audit(
        "mcp_image_gate_beat1",
        detail={
            "resolved": False,
            "plan": "",
            "skipped": True,
            "skip_reason": "attested_without_bytes",
            "attested": ctx.attested,
        },
    )


def _is_attested_skip(ctx: _GateCtx, plan: str) -> bool:
    """True when beat 1 was skipped on attestation alone (a count, no bytes)."""
    return bool(
        not plan
        and not str(ctx.jira_content_json or "").strip()
        and not ctx.attached_images
        and ctx.attested
        and image_gate._gate_jira_source(ctx.text)
    )


# --- Jira image gate, BEAT 1 (QA_IMAGE_GATE_ENABLED) ------------------- #
# Placed AFTER the duplicate-prep guard (an already-open prep is the
# cheaper, more urgent signal) and BEFORE the fetch
#
# 2026-08-31 (F3): that ordering is still right, but it MASKED this gate
# -- a tester re-running a ticket saw only the duplicate warning and never
# the screens question, which then cost a further round trip after the
# proceed_anyway retry. Both duplicate clarifies now append
# _pending_image_gate_hint, whose condition MIRRORS the `if` below. Change
# one and you must change the other, or the hint promises a question that
# never comes (or stays silent about one that does). -- which is the whole
# point: the "I cannot read Jira images" disclosure used to be appended to a
# payload the host had ALREADY been told to generate from.
#
# REACHABILITY: handle_generate_test_cases re-routes into this handler in
# host mode, so both beats fire on qa_generate_test_cases too -- which is
# why that handler and that tool forward these same four arguments.
#
# Jira sources only, FIRST call only. A plain feature / web / Swagger
# source skips this entirely, and a caller that already stated a plan or
# already supplied images is never asked twice -- `attached_images`
# counts, which is what keeps the Feature-Analysis `jira_mobile` route
# (it captures screens FIRST, then reaches this handler through the
# host-mode reroute) from being asked where its screens come from. NO prep is saved on a
# gated round, so the duplicate-prep guard sees nothing new on the
# follow-up call and a re-ask can never start a second full generation.
#
# "elicit" (audit detail):
# 2026-08-09 (FIX 3): {"resolved": false, "plan": ""} did not say
# WHY, so a client that cannot show an elicitation, a tester who
# declined, and an install with QA_MCP_ELICIT_ENABLED off all read
# identically. "<enum>/<text>" over the two tiers, with
# "disabled/disabled" for the flag-off / no-callback case --
# "unavailable/unavailable" is the genuine client-capability limit,
# which is what the 2026-08-09 run actually was.
#
# FIX 3 (review H4): the status decides HOW the fallback explains
# itself. It must not assert a client limitation when elicitation was
# simply turned off on this server, or when the tester declined.
#
# 2026-08-31 (finding 1): beat 1 was skipped on ATTESTATION ALONE -- a
# count with no bytes behind it and no plan. The chat attachments stay
# in the host's context by design, so this server can never verify the
# number; what it can do is stop the skip being invisible. The honest
# bypass (`image_gate_ack=true`) is disclosed in the finished payload,
# so the unverifiable one should not be quieter than the honest one.
async def image_gate_beat1(args: Beat1Args) -> _Beat1:
    """Jira image gate beat 1: elicit the source plan, or audit an attested skip."""
    ctx = args.ctx
    probe = GateProbe(
        ctx.text, args.plan, ctx.jira_content_json, ctx.attached_images, ctx.attested
    )
    if host()._image_gate_would_fire(probe):
        return await _beat1_fire(args)
    if _is_attested_skip(ctx, args.plan):
        await _beat1_attested_skip(ctx)
    return _Beat1(args.plan, None)


_image_gate_beat1 = alias(sys.modules[__name__], "image_gate_beat1")
