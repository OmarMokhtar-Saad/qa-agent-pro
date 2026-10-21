"""Shared packet pieces: the base dict, the screen block and the observation key."""

from __future__ import annotations

import logging

from tools.mobile import actions as actions_mod
from tools.mobile import knowledge_inject, perception, render, run_store, screen_audit
from tools.mobile.run_packets_text import _SYSTEM_PROMPT
from tools.mobile.screen_progress import progress_line
from tools.untrusted import _GUARD, wrap_untrusted

logger = logging.getLogger("tools.mobile.run_packets")

#: What a packet builder can hit on a malformed view, screen or trace: a missing
#: attribute or key, a wrong type or value. Anything else is a real bug and surfaces.
_PACKET_ERRORS = (AttributeError, KeyError, IndexError, TypeError, ValueError, OSError)


def _packet_base(run_id: str, tc_id: str = "") -> dict:
    packet = {
        "run_id": str(run_id or ""),
        "tc_id": str(tc_id or ""),
        "system_prompt": _SYSTEM_PROMPT,
        "untrusted_data_notice": _GUARD,
        "vocabulary": actions_mod.describe_vocabulary(),
        "response_schema": actions_mod.response_schema(),
    }
    # Saved knowledge about the app: the key is ABSENT when there is none, so a
    # run without knowledge builds the same packet as before.
    known = knowledge_inject.stable_block(run_id)
    if known:
        packet["app_knowledge"] = known
    return packet


def _accessibility_wrap(body: dict) -> str:
    note = screen_audit.summary_text(body.get("accessibility"))
    return wrap_untrusted("accessibility", note, limit=4000) if note else ""


# The pruned screen as a wrapped prompt block. Never the raw XML.
#
# The accessibility audit rides ALONGSIDE it, in its own wrapper, because the
# two are different claims: the element lines say what is on the screen, and
# the audit says whether a person could use it. It is emitted even when
# `to_prompt_block` returns "" -- a canvas screen has no element lines and is
# exactly the screen whose audit says "no nodes, nothing was audited, look at
# the picture instead".
#
# The names it quotes are DEVICE text, so it goes through `wrap_untrusted`
# like everything else here. Nothing in this block is a verdict, and the
# summary says so in its own words.
def _screen_block(screen: object, run_id: str = "") -> str:
    body = screen if isinstance(screen, dict) else {}
    if isinstance(body.get("content"), dict):
        body = body["content"]
    block = perception.to_prompt_block(screen)
    wrapped = _accessibility_wrap(body)
    # I3: the update-flow hint rides on the screen block for App Tester under
    # either package id, and is "" (the old bytes) for every other app.
    hint = render.app_tester_hint(body.get("package"))
    rules = knowledge_inject.screen_rules(run_id, screen) if run_id else ""
    parts = (block, wrapped, progress_line(body), hint, rules)
    return "\n".join(part for part in parts if part)


# The key of THIS LOOK at the screen this packet describes, or ``""``.
#
# Delegates to ``run_store.observation_id`` -- "THE one producer of this
# string" -- and mints nothing. Three builders need the value, and three copies
# of a hand-rolled ``screen_id + "-" + hash[:12]`` is precisely how the writer
# and the reader end up keying on two different strings.
#
# NOT the bare ``screen_id``: that is the DEDUP identity and is invariant
# across the turns of a conversation, so it names a screen and never says what
# was on it. A hashless screen degrades to the bare id inside the producer,
# where every existing reader already expects that.
#
# A ROUTING value, not packet content: ``mcp_handlers._mobile_packet_text``
# reads it to file this turn's PNG under the key the report's frames resolve
# on, and pops it off its own copy before the packet is rendered -- so nothing
# here changes a byte of what the model reads.
def _observation_key(screen: object) -> str:
    try:
        return run_store.observation_id(screen)
    except (
        _PACKET_ERRORS
    ):  # pragma: no cover - the store never raises; a packet may not
        logger.warning("mobile_run._observation_key failed", exc_info=True)
        return ""
