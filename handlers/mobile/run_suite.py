"""Part of the mobile_run split (see handlers/mobile/run.py): suite."""

from __future__ import annotations

from dataclasses import dataclass

import tools.mobile.session_runs as session_runs
from handlers.mobile.run_cases import _mobile_resumable_run
from handlers.mobile.run_hooks import mobile_run
from handlers.mobile.run_request import _MobileStartContext
from tools.mobile.session_runs import RunEnv, RunTarget, SuiteOpts
from tools.untrusted import single_line as _safe


# Notes for ``_mobile_start_suite`` (moved out of the body to keep it short):
# E5 (2026-09-04): the same cases against the same app, again. Eight runs
# of ONE case were started in seventeen minutes because nothing said that
# the run just abandoned was the one to go back to. Answer that here,
# before a ninth is planned -- and only for a start that carries cases, so
# an explore run and a resume are untouched.
# Every case-bearing start, filters included.
#
# A re-run of failures is covered without a special case: the run it would
# duplicate stores the cases it KEPT, so a second re-run hashes that same
# set and matches `case_signature`. An earlier version of this comment said
# the match came from `source_signature`; that clause was proved dead and
# deleted, and the sentence is corrected rather than left describing code
# that is gone.
@dataclass(frozen=True)
class _SuiteAsk:
    """The collected cases of a start-menu choice and how to plan them."""

    picked: str
    collected: list
    filters: dict
    new_run: bool


async def _mobile_start_suite(
    ask: _SuiteAsk, ctx: _MobileStartContext
) -> tuple[str, bool]:
    """Offer the earlier run, else plan the suite run and hand it off."""

    if not ask.new_run:
        existing = _mobile_resumable_run(ctx.package, ask.collected)
        if existing:
            return _mobile_repeat_offer(existing), False

    planned = _plan_suite(ask, ctx)
    if planned.get("error"):
        return "⚠️ " + _safe(planned["error"], 400), False
    return await mobile_run.mobile_hand_off(
        str((planned.get("content") or {}).get("run_id") or ""),
        pre_run_owner=ctx.owner,
        progress=ctx.progress,
    )


def _plan_suite(ask: _SuiteAsk, ctx: _MobileStartContext) -> dict:
    """Plan the suite run for the collected cases against the device in *ctx*."""

    env = RunEnv(
        device=ctx.device_facts,
        locale=ctx.locale,
        capture=ctx.capture,
        reset_app=ctx.reset_app,
    )
    return session_runs.plan_suite_run(
        ask.collected,
        RunTarget(ctx.package, ctx.serial, ctx.avd, env),
        SuiteOpts(source=ask.picked, filters=ask.filters),
    )


# Notes for ``_mobile_repeat_offer`` (moved out of the body to keep it short):
#
#     It offers rather than refuses: a tester who really does want a fresh run
#     says so with `new_run=true`, which is named here so the next call needs no
#     guesswork.
#
def _mobile_repeat_offer(existing: dict) -> str:
    """These cases are already a run. Say which, and how to pick it up."""
    run_id = str(existing.get("run_id") or "")
    state = str(existing.get("state") or "unknown")
    done = int(existing.get("done") or 0)
    total = int(existing.get("total") or 0)
    return (
        "## These cases are already a run\n\n"
        "`" + run_id + "` is running the same cases against the same app and is "
        "not finished — state **"
        + state
        + "**, "
        + str(done)
        + " of "
        + str(total)
        + " case(s) done. Nothing new was started.\n\n"
    ) + _repeat_options(run_id)


def _repeat_options(run_id: str) -> str:
    """The three ways forward the repeat offer lists."""
    return (
        '- **Carry on with it** — call `qa_mobile_test` with `run_id="'
        + run_id
        + '"`. Every case it already finished is on disk and is not '
        "handed out again.\n"
        "- **See where it stands first** — `qa_mobile_status` with that run id "
        "reads it back from disk and changes nothing.\n"
        "- **Start a fresh run anyway** — the same call again with "
        "`new_run=true`. Do that only if the tester asked for it: a second run "
        "of the same cases produces a second report of the same work."
    )
