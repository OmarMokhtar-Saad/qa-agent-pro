"""Part of the mobile_run split (see handlers/mobile/run.py): source."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from handlers import _common, _elicit
from tools.mobile import render as mobile_render

logger = logging.getLogger("tools.mcp_handlers")


@dataclass(frozen=True)
class _SourceCall:
    """The inputs of one run-source pick."""

    source: str
    suite_id: str = ""
    goal: str = ""
    cases: str = ""
    choose: _common.ChooseCb = None


# A `given` source may be answering a DIFFERENT question that already consumed
# it earlier in this same call -- e.g. `source="installed_package"` is a valid
# INSTALL-menu key, `_mobile_app_stage` already used it, and it matches no
# RUN-menu key. Falling straight to "ask" ignored `goal`/`cases`/`suite_id`
# even when one of them already named the run lane -- the D3 field defect, so
# `_mobile_pick_source` falls through to the implied-sources check. With no
# implied source either it asks, as for a genuinely unmatched answer.
# Notes for ``_mobile_pick_source`` (moved out of the body to keep it short):
#     dialog, or ``""``.
#
#     ``""`` means "ask", and the markdown menu is the tested path: MCP
#     elicitation arrives collapsed-and-required in some editors, so the dialog is
#     an optimisation and the menu is the product.
#
#     **A NUMBER is no longer accepted.** The host re-presents that menu in its
#     own question UI, which relabels (a/b/c/d) and reorders it, so a returned
#     POSITION selected a lane by index -- "explore" shown first started
#     ``current_suite``, and a tester saw "4 2 1 3". Only a key identifies an
#     option, and ``render.source_for_label`` accepts the key or the exact line
#     and nothing else.
#
#     **An argument the tester has ALREADY given decides the source.** This read
#     only ``source``, so a tester who pasted their own cases or described a goal
#     was shown the six-way menu anyway while the answer sat unread in the call.
#     ``cases``, ``goal`` and ``suite_id`` each name exactly one lane
#     (``render.SOURCE_IMPLICATIONS``), so the menu is asked only when NONE of
#     them is set. When two disagree this returns ``""`` and the caller renders
#     the NARROWER question rather than the menu -- guessing which one the tester
#     meant is the failure this whole function exists to avoid.
#
async def _mobile_pick_source(call: _SourceCall) -> str:
    """Resolve the start choice: an argument, what the tester already said, a"""

    given = str(call.source or "").strip()
    matched = mobile_render.source_for_label(given) if given else ""
    if matched:
        return matched
    implied = mobile_render.implied_sources(
        suite_id=call.suite_id, goal=call.goal, cases=call.cases
    )
    if len(implied) == 1:
        return implied[0]
    if implied or given:
        return ""
    return await _mobile_pick_source_dialog(call.choose)


async def _mobile_pick_source_dialog(choose: _common.ChooseCb) -> str:
    """The run-menu dialog: the chosen source key, or ``""`` to show the menu."""

    picked = await _elicit.elicit_choice(
        choose, "What should the emulator run?", mobile_render.source_labels()
    )
    if picked.status == _common.CHOSEN:
        # Which path answered is logged (fix round 3, item 6); the label is a
        # server-authored menu entry, not tester text.
        logger.info("mcp emulator source picked via dialog: %r", picked.value)
        return mobile_render.source_for_label(picked.value or "")
    logger.info(
        "mcp emulator source: %s",
        "dialog timed out, text menu shown"
        if getattr(picked, "timed_out", False)
        else "no dialog answer, text menu shown",
    )
    return ""
