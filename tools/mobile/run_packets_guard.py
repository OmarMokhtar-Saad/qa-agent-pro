"""Tester and guard packets: asking the tester for one field or a go-ahead."""

from __future__ import annotations

from tools.mobile.replay import guard as guard_mod
from tools.mobile.run_packets_args import TesterAsk
from tools.mobile.run_packets_base import _PACKET_ERRORS, logger

#: The fail-safe ask for a sentinel no consumer registered. A NAMED constant so
#: the ratchet can assert a registered sentinel does NOT get it: a mutation that
#: bypassed the registry entirely landed every sentinel here, and every wording
#: assertion passed, because this text is deliberately inoffensive. "Not false"
#: is the floor, not the bar -- the specific remedy is the product.
GUARD_ASK_SCREEN_GENERIC = (
    "The replay stopped before a press: this screen cannot be vouched for, so "
    "what that press would submit cannot be judged. Nothing was touched. "
    "Re-sending the same press stops here again -- read the stop's `detail` "
    "for what was seen, and plan a different action."
)

_ASK_NOT_THE_APP = (
    "The replay stopped before a press: the screen in front is not the "
    "app under test, so what that press would submit cannot be judged. "
    "Nothing was touched. Re-sending the same press stops here again -- "
    "bring the app back with `launch`, or send `back` if a dialog is in "
    "front of it, and then carry on in the same script. The stop's "
    "`detail` names both packages."
)

_ASK_NOT_FULLY_SEEN = (
    "The replay stopped before a press: this screen has more elements "
    "than one packet carries, so what that press would submit cannot "
    "be judged. Nothing was touched. Re-sending the same press stops "
    "here again -- plan a TAP on the control this case means instead, "
    "which is judged by its own label. Ask the tester only if you "
    "cannot tell which control that is."
)

_ASK_CONTROL_NOT_READ = (
    "The replay stopped before acting: the element under that touch was "
    "not read in full -- the app does not expose its text to the "
    "accessibility layer this lane reads, so what the action would "
    "trigger cannot be judged. Nothing was touched. Repeating it stops "
    "here again, and no re-wording of the script recovers a string the "
    "app never exposed -- ask the tester to turn accessibility on for "
    "the app under test, or to use an instrumented build. Until then, "
    "plan a tap on a control the packet does name."
)

_TESTER_FIELD_ASK = (
    "Ask the tester for this one field, in chat, and pass the "
    "value back in `tester_input` with `tester_input_field` set "
    "to the field name. It is typed straight into the app and "
    "is not stored anywhere -- not in the report, not in the "
    "checkpoint, not in the audit log."
)

_TESTER_NEVER_DO = (
    "Do not invent a value, do not reuse one from an earlier run, "
    "and do not repeat the value back in your own message."
)


# Sentinel -> what the MODEL is told, for every screen-level stop.
#
# A REGISTRY, matching ``executor.screen_stop_details`` at the other
# consumer. Keyed off the passed-in module rather than a module-level import,
# because the import here is deliberately lazy -- see :func:`_guard_ask`.
#
# Every sentence must survive the 60-character truncation
# ``build_tester_request`` applies to the TERM, name a move that can actually
# work, and avoid the two claims that are false for any screen stop. The
# ratchet grades this dict against :func:`executor.screen_sentinels`.
def screen_asks(guard_mod) -> dict:
    return {
        guard_mod.SCREEN_NOT_THE_APP: _ASK_NOT_THE_APP,
        guard_mod.SCREEN_NOT_FULLY_SEEN: _ASK_NOT_FULLY_SEEN,
        guard_mod.SCREEN_CONTROL_NOT_READ: _ASK_CONTROL_NOT_READ,
    }


def _control_ask(guard_term: str) -> str:
    return (
        "The replay stopped in front of a control that looks irreversible ("
        + guard_term
        + "). Nothing was tapped. Ask the tester whether to go ahead, and "
        "re-submit the same script only if they say yes."
    )


# What the model is told about a guard stop, per REASON.
#
# Only ONE of the stops that reach here is about a control -- a matched
# lexicon term. Every SCREEN-LEVEL stop (the ``SCREEN_*`` sentinels the
# executor exports) is about the screen instead, so the fallback's two claims
# are both false for it: there is no control to name, and "re-submit the same
# script if the tester agrees" is deterministically wrong -- the same press
# meets the same screen and stops again, so the only door that wording leaves
# open is turning the guard off for the whole script, including a real
# Confirm button. Each sentinel therefore needs its OWN branch, naming a
# remedy that can actually work.
#
# NO COUNT IN THIS DOCSTRING, deliberately. It said "two stops" while three
# reached here: ``SCREEN_NOT_THE_APP`` was added at the producer
# (``replay.guard.screen_hit``) and wired into only one of that value's two
# consumers, and this one fell through to the control wording -- telling the
# model a non-existent control had been hit and to re-send a script that
# re-stops. The ratchet that now fails at authoring time instead is
# ``tests/mobile/test_mobile_sentinel_ratchet.py``, which derives the
# sentinel list from the executor rather than listing it.
#
# Imported lazily: ``executor`` owns the term, this module owns the wording,
# and a module-level import would put an agent's import at the top of a file
# the executor's own callers import.
def _guard_ask(guard_term: str) -> str:

    said = screen_asks(guard_mod).get(guard_term)
    if said is not None:
        return said
    if not guard_term or guard_mod.destructive_hit(guard_term) != guard_term:
        # A term this module has no words for, and one the lexicon does not
        # match either -- so it is not a control, and the fallback's two
        # claims would both be false. True and unhelpful beats false and
        # actionable. See executor.guard_detail, which asks the same question
        # at the other consumer.
        logger.error("mobile_run: screen sentinel %r has no ask registered", guard_term)
        return GUARD_ASK_SCREEN_GENERIC
    return _control_ask(guard_term)


def _tester_ask_text(guard_term: str) -> str:
    return _guard_ask(guard_term) if guard_term else _TESTER_FIELD_ASK


# Ask the TESTER for one field, or for a go-ahead past the guard.
#
# Carries the field NAME and the question. It never carries, and has no way to
# carry, a value: the value is supplied on the next submit call and typed
# straight to the device.
def build_tester_request(ask: TesterAsk) -> dict:
    try:
        return {
            "run_id": str(ask.run_id or ""),
            "tc_id": str(ask.tc_id or ""),
            "kind": "tester",
            "field": str(ask.field or "")[:80],
            "prompt": str(ask.prompt or "")[:600],
            "guard_term": str(ask.guard_term or "")[:60],
            "ask_the_tester": _tester_ask_text(str(ask.guard_term or "")),
            "never_do": _TESTER_NEVER_DO,
        }
    except _PACKET_ERRORS:  # pragma: no cover - defensive
        logger.warning("build_tester_request failed", exc_info=True)
        return {}
