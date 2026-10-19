"""Part of the mobile_run split (see handlers/mobile/run.py): explore."""

from __future__ import annotations

import tools.mobile.session_runs as session_runs
from handlers.mobile.run_hooks import mobile_run
from handlers.mobile.run_request import _MobileStartContext
from tools.mobile import charter as mobile_charter
from tools.mobile import render as mobile_render
from tools.mobile import run_packets as mobile_run_agent
from tools.mobile.session_runs import ExploreOpts, RunEnv, RunTarget
from tools.tool_briefs import MOBILE_BRIEF
from tools.untrusted import single_line as _safe


def _mobile_charter_intake(parsed: dict, package: str) -> str:
    """The charter-intake packet plus the once-per-chat tool brief."""

    # The intake is the FIRST mobile packet and has no run to memoise
    # a briefing on, so it always carries the mobile brief (same
    # heading `_mobile_packet_text` uses).
    return (
        mobile_render.packet_block(
            mobile_run_agent.build_charter_intake(
                package=package, charter=parsed.get("raw") or {}
            )
        )
        + "\n\n### Tool brief (sent once per chat)\n"
        + MOBILE_BRIEF.strip()
    )


# Notes for ``_mobile_plan_explore`` (moved out of the body to keep it short):
# THE RAW (schema-stripped) BODY, never the normalised `terms`.
# `new_state` normalises what it is given, and `normalize` STRIPS
# DEFAULTS_KEY/REJECTED_KEY (the anti-spoof strip) and then re-derives
# the verdict against a body whose every field is already filled -- so
# handing it `terms` records "no defaults used" for EVERY run started
# through this handler, including the goal-only start whose whole
# justification is that the report names every default it used.
# Measured on the merge tree: `terms` -> [], the raw body ->
# ['depth','scope','destructive','budget','stop_on']. `parse` already
# injected the caller's `goal` into this body, so nothing is lost by
# passing it. Normalise ONCE, at the one writer. Graded at the HANDLER
# by M14 / test_a_goal_only_start_through_the_handler_names_its_defaults.
def _mobile_plan_explore(goal: str, parsed: dict, ctx: _MobileStartContext) -> dict:
    """Plan the explore run from the RAW charter body (see the note below)."""

    env = RunEnv(
        device=ctx.device_facts,
        locale=ctx.locale,
        capture=ctx.capture,
        reset_app=ctx.reset_app,
        permissions=ctx.permissions,
    )
    return session_runs.plan_explore_run(
        goal,
        RunTarget(ctx.package, ctx.serial, ctx.avd, env),
        ExploreOpts(charter=parsed.get("raw") or {}),
    )


# Notes for ``_mobile_start_explore`` (moved out of the body to keep it short):
# THE CHARTER (step 4). `charter.parse` is the only reader of the
# host-submitted string: size-capped, json.loads only, every field
# coerced to the server's typed schema.
#
# Audit item 4: the destructive hint reaches the LIVE reply. (Item 5,
# an out-of-enum value, never gets this far: `charter.parse` above
# refuses it by name, listing the valid choices.) Read off
# `raw_charter`, never `terms`: same M11 reason `normalize`'s own
# docstring gives.
#
# Neither a charter nor a goal: ASK, once, with the SERVER's own
# questions, as a packet KIND on this same return path -- never a
# tool, because intake is only ever the first packet of a run.
# This replaces the bare "give the exploratory goal in a sentence"
# refusal. A start that already carries a goal is untouched: it
# runs under the default charter, whose defaults the report NAMES.
# THE RAW BODY, never `terms`: normalisation fills every field, so
# handing the builder the normalised charter makes four of the five
# questions read as ANSWERED and the intake asks ONE -- measured.
async def _mobile_start_explore(
    charter: str, goal: str, ctx: _MobileStartContext
) -> tuple[str, bool]:
    """The "explore" start-menu choice: parse the charter, then plan and hand off."""

    parsed = mobile_charter.parse(charter, goal=goal)
    if parsed.get("error"):
        return "⚠️ " + _safe(parsed["error"], 300), False
    terms = parsed.get("content") or {}
    disclosures = mobile_charter.destructive_hint(
        parsed.get("raw") or {}, terms.get("goal") or goal
    )
    if not str(terms.get("goal") or ""):
        return _mobile_charter_intake(parsed, ctx.package), False
    planned = _mobile_plan_explore(terms.get("goal") or goal, parsed, ctx)
    if planned.get("error"):
        return "⚠️ " + _safe(planned["error"], 300), False
    return await _explore_hand_off(planned, disclosures, ctx)


async def _explore_hand_off(
    planned: dict, disclosures: str, ctx: _MobileStartContext
) -> tuple[str, bool]:
    """Hand the planned explore run off; the destructive hint leads the reply."""
    handed_text, handed_started = await mobile_run.mobile_hand_off(
        str((planned.get("content") or {}).get("run_id") or ""),
        pre_run_owner=ctx.owner,
        progress=ctx.progress,
    )
    return (
        (disclosures + "\n\n" + handed_text) if disclosures else handed_text
    ), handed_started
