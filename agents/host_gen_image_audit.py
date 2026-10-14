"""Host mode: the image-plan completion nudges and their audit row (C3c).

Moved from handlers/generate.py. A plan that PROMISED images and has not
delivered them yet gets ONE actionable instruction per missing channel, on
every call until it is satisfied. `image_gate_ack=true` is always the way out,
so this can never dead-end. Still no fetch, no prep.
"""

from __future__ import annotations

import logging

from agents.host_gen_args import NudgeAudit
from agents.host_gen_hooks import GenerateHooks
from config.settings_guard import never_raise

logger = logging.getLogger("agents.host_mode")


_BOTH_SENTENCE = (
    " You chose BOTH channels, so in the same call also pass the "
    "`capture_ids` returned by `qa_capture_screens`."
)


# Attach nudge:
# I1 (2026-08-10): this exit is tester-visible and used to leave no
# trace at all, so a loop of them was invisible in telemetry.
def attach_nudge_text(plan: str) -> str:
    """The "Attach the screenshots now" nudge, plus the BOTH-channels sentence."""
    _both = _BOTH_SENTENCE if plan == "jira_both" else ""
    return (
        "## 📎 Attach the screenshots now\n\n"
        "Ask the user to attach the screen(s) to THIS chat (as many "
        "as they have), then call the SAME tool again with the SAME "
        "`feature_or_url`, `source_plan='" + plan + "'` and "
        "`attached_image_count=<how many they attached>`. If this "
        "call already carried `jira_content_json`, re-send that SAME "
        "value -- do NOT fetch the ticket again. The images stay in "
        "YOUR context -- no image bytes are sent to this server -- "
        "and the generation payload will ask you to describe them "
        "before you write the cases. If those screens are not "
        "available after all, call again with `image_gate_ack=true` "
        "and I will generate from the ticket text alone and say so "
        "in the reply." + _both
    )


# Capture nudge:
# I1 (2026-08-10): same finding as the attach nudge above. The
# unresolved-id count is the useful signal here -- it separates
# "never captured anything" from "sent ids that expired".
def capture_nudge_text(plan: str) -> str:
    """The "Capture the screens first" nudge for a device plan without captures."""
    return (
        "## 📸 Capture the screens first\n\n"
        "Call `qa_capture_screens` (it picks the device, offers a "
        "Rescan, and captures screen after screen), then call the "
        "SAME tool again with the SAME `feature_or_url`, "
        "`source_plan='" + plan + "'` and the `capture_ids` it "
        "returns -- plus, if this call already carried "
        "`jira_content_json`, that SAME value again (do NOT fetch "
        "the ticket a second time). Those ids survive the Jira fetch "
        "directive and any failed attempt, so they can be re-sent "
        "unchanged. If no device is reachable, call again with "
        "`image_gate_ack=true` instead and I will generate from the "
        "ticket text alone and say so in the reply. Nothing has been "
        "prepared yet, so this costs no generation."
    )


# The COUNT is the real total; the id LIST is the capped sample. The row used
# to write len(ids) -- the capped number -- as `unresolved_captures`, so a
# 27-id miss read as 8 in telemetry (round-6 review).
def _nudge_detail(a: NudgeAudit, all_ids: list, cap: int) -> dict:
    ids = [str(x)[:64] for x in all_ids][:cap]
    unresolved = len(all_ids)
    return {
        "plan": str(a.plan or "")[:32],
        "channel": a.channel,
        "unresolved_captures": unresolved,
        "capture_ids": ids,
        "capture_ids_truncated": unresolved > len(ids),
    }


# 2026-08-10 (I1): both nudge returns handed the tester a clarify and left
# NOTHING behind. A NEW event id rather than a `mcp_prepare_rejected` variant:
# that row means "_prepare_generation refused this source". `missing_ids` are
# HOST-supplied capture ids, so they are length- and count-capped here.
async def audit_image_plan_nudge(a: NudgeAudit, hooks: GenerateHooks) -> None:
    """Audit + log ONE image-plan completion nudge exit. Never raises."""
    # pragma: no cover - a disclosure never breaks a prepare
    with never_raise(logger, "image-plan nudge audit failed", exc_info=True):
        cap = hooks.capture.max_missing_shown()
        detail = _nudge_detail(a, list(a.missing_ids or []), cap)
        await hooks.common.audit("mcp_image_plan_nudge", detail=detail)
        logger.info(
            "image-plan nudge: plan=%s channel=%s unresolved=%d",
            detail["plan"],
            a.channel,
            detail["unresolved_captures"],
        )
