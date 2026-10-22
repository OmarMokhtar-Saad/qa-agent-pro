"""The Feature Analysis reply header and the vision-loss disclosure."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise
from handlers.feature_analysis_types import _FaCapture

logger = logging.getLogger("tools.mcp_handlers")


# Disclose that captured device screens reached NO vision description.
#
# Ledger row `image_description.describe_images`, terminal status
# `disabled (disclosed)` (residue sub-phase R3). That token is only TRUE if
# something discloses, and until now nothing did: the reply's existing header
# is gated on `screen_descriptions`, so when the descriptions are missing it
# simply goes quiet and the tester is left assuming the screens were read.
#
# Fires ONLY when screens were actually captured and NOTHING came back
# (Phase 3b's narrowing rule -- never claim a loss that did not happen). Since
# the 2026-08-15 migration the mobile branch ATTACHES its screens instead of
# describing them, so the ordinary outcome is the `forwarded_screens` notice
# at the call site and THIS disclosure is the residual: screens captured,
# nothing attached and nothing described. It therefore has ONE cause and one
# wording. The second branch -- "the server-LLM switch refused the call" --
# was DELETED on 2026-08-15 together with the switch: `llm.server_llm_enabled`
# is a True constant, so that branch was unreachable, and its text named two
# settings that no longer exist.
#
# Never raises: a disclosure must not be able to break a prepare.
def _fa_vision_disclosure(screens_captured: int) -> str:
    with never_raise(logger, "_fa_vision_disclosure failed", exc_info=True):
        if not screens_captured:
            return ""
        why = (
            "this server produced no description for them — it makes no "
            "vision call of any kind, and the screens were not attached to "
            "the reply either — describe them yourself in the chat"
        )
        return (
            f"> ⚠️ {screens_captured} screen(s) were captured but NOT "
            f"described: {why}. The analysis is written from the text above, "
            "not from the screens.\n\n"
        )
    return ""


def _fa_reply_header(mode: str, capture: _FaCapture) -> str:
    """The mode line and the honest note on what happened to the screens."""
    header = ""
    if mode != "jira":
        header = f"_Mode: {mode} · screens captured: {capture.screens_captured}_\n\n"
    if capture.forwarded:
        # Owner rule: no screenshot ever reaches the host.
        header += (
            "> ℹ️  "
            f"{len(capture.forwarded)} screen(s) were captured but screenshots "
            "are never shared with you. This server made NO vision call — "
            "describe the screens from the text above.\n\n"
        )
    elif capture.screens_captured:
        # Screens were captured and NOTHING described them (the server makes
        # no vision call): the tester must not be left believing they were read.
        header += _fa_vision_disclosure(capture.screens_captured)
    return header
