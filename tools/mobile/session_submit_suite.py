"""Mobile session submit: the suite lane (split from session_submit)."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise
from tools.mobile import actions as actions_mod
from tools.mobile import case_runner, held_inputs, scheduler
from tools.mobile.replay.context import Context
from tools.mobile.session_core import STATE_GATE, STATE_REPORT, STATE_RUNNING
from tools.mobile.session_explore_stop import _prior_guard_stop
from tools.mobile.session_packets import _finish_evidence
from tools.mobile.session_runs import load_case
from tools.mobile.session_submit_args import _SuiteTarget

logger = logging.getLogger("tools.mobile.session")

SINGLE_ACTION_NOTICE = (
    "This script carried one action. A script may carry up to "
    "{max_actions} -- a tap, the type it triggers and the assert that "
    "checks it usually fit in one script together, and each extra round trip "
    "through this tool is one the tester's own client timer is also paying "
    "for. Batch the next few actions you already know you want when they do "
    "not depend on an unpredictable reply."
)


# "" unless *raw_script* parses to exactly one action.
#
# Never raises and never refuses: read AFTER `case_runner.submit_case`
# already validated and replayed the same script, so a parse failure here
# can only mean the script changed between the two reads, which never
# happens on this call's own object. Audit item 3 -- the schema already
# allowed up to `actions.MAX_MODEL_ACTIONS`; nothing said so where a planner
# would see it before scripting one action per submit. VERIFIED (not
# UNVERIFIED): `actions_mod.parse_script`'s own docstring says it
# "[a]ccepts the packet shape ({\"actions\": [...]}), a bare list, or a
# JSON string of either" -- `tools/mobile/actions.py:636-642` -- so this
# call on the raw string `submit()` already received is exactly one of
# the three documented shapes, not a new assumption.
def _single_action_notice(raw_script: object) -> str:
    """ "" unless *raw_script* parses to exactly one action."""
    with never_raise(
        logger, "mobile.session._single_action_notice failed", exc_info=True
    ):
        parsed = actions_mod.parse_script(raw_script)
        script = parsed.get("content")
        if script is not None and len(script.actions) == 1:
            return SINGLE_ACTION_NOTICE.format(
                max_actions=actions_mod.MAX_MODEL_ACTIONS
            )
    return ""


def _suite_state(content: dict, follow: dict) -> str:
    """The run state after a suite-lane answer: running, report or gate."""
    if content.get("packet"):
        return STATE_RUNNING
    if follow.get("finished"):
        return STATE_REPORT
    return STATE_GATE if follow.get("gate") else STATE_RUNNING


async def _finish_report(target: _SuiteTarget) -> None:
    """The run reached its report: gather the evidence, drop the held inputs."""

    await _finish_evidence(target.run_id, target.body)
    held_inputs.forget(target.run_id, secrets=False)  # the finish scan reads them


def _suite_reply(target: _SuiteTarget, state: str, parts: tuple) -> dict:
    """The reply of a suite-lane submit; *parts* is (case, next, notice)."""
    content, follow, notice = parts
    return {
        "error": None,
        "content": {
            "state": state,
            "case": content,
            "packet": content.get("packet"),
            "next": follow,
            "field": target.field,
            "resolved": target.body,
            "notice": notice,
        },
    }


async def _submit_suite(target: _SuiteTarget, raw_script: object, ctx: Context) -> dict:
    """Replay one suite-lane answer and build the verdict plus next-step reply."""
    # Scoped to THIS tc_id, not a run-wide scan -- see `_prior_guard_stop`.
    ctx.prior_guard_stop = _prior_guard_stop(target.run_id, target.tc_id)
    loaded = load_case(target.run_id, target.tc_id)
    if loaded.get("error"):
        return loaded
    result = await case_runner.submit_case(
        case_runner.Submission(target.run_id, loaded["content"], ctx, raw_script)
    )
    if result.get("error"):
        return result
    content = dict(result.get("content") or {})
    follow = (scheduler.next_case(target.run_id) or {}).get("content") or {}
    state = _suite_state(content, follow)
    if state == STATE_REPORT:
        await _finish_report(target)
    parts = (content, follow, _single_action_notice(raw_script))
    return _suite_reply(target, state, parts)
