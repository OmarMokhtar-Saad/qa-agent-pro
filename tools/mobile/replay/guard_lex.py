"""Replay guard lexicon: which words make a control destructive."""

from __future__ import annotations

from tools.mobile import actions as actions_mod
from tools.mobile import perception
from tools.mobile.replay.elements import _tokens
from tools.mobile.replay.guard_consts import (
    GUARD_DETAIL,
    NEW_ELEMENT_KINDS,
    NON_ACTUATING_OPS,
)

# Whole-word terms that mean "this tap is not undoable". English plus the Latin
# script variants a tester is likely to meet on a localised build.
#
# NO Arabic (or other non-Latin) literals appear here on purpose: the programme's
# release gate greps this diff for Arabic literals to prove nothing leaked from
# the private reference project, and a legitimate lexicon entry would be
# indistinguishable from a leak. Adding non-Latin variants is a follow-up that
# must land with its own provenance note, NOT a drive-by.
DESTRUCTIVE_LEXICON = (
    "confirm",
    "confirmar",
    "confirmer",
    "delete",
    "eliminar",
    "supprimer",
    "remove",
    "erase",
    "wipe",
    "uninstall",
    "pay",
    "pagar",
    "payer",
    "purchase",
    "checkout",
    "buy",
    "submit",
    # 2026-09-04: the bare token "send" WAS here, and it made the lane unusable
    # for the app it was validated on. The Send control of a chat composer
    # resolves to "View Send", the guard stopped it, and nothing can get past
    # the guard: no caller sets ``guard_destructive`` False, so the
    # "resubmit if the tester agrees" the tester-request packet offers is
    # stopped identically on the resubmit and the case dead-ends.
    #
    # The contract this lexicon enforces is "this tap is not undoable", and a
    # chat message is not. So the token became the phrases that ARE about
    # money.
    #
    # THE COMPENSATING CONTROL FIRST CLAIMED HERE DOES NOT EXIST, and saying so
    # is the point. The claim was that every money word below still stands
    # alone, so each transaction path keeps two independent catchers. A review
    # that ran the code refuted it: this guard judges the RESOLVED ELEMENT and
    # the text drawn inside it, never the rest of the screen -- so on a
    # transfer confirmation headed "Transfer to Ahmed", a button labelled
    # plainly "Send" is not stopped, and neither are "Send $50", "Send to
    # Ahmed" or a rid of `btn_send`.
    #
    # THE RESIDUAL, stated rather than implied, exactly as the no-label residual
    # below is: a send control on a money screen whose own label says only
    # "Send" is not stopped by this lexicon. For a TAP the guard stays on the
    # named element and the control under the finger -- widening it to the
    # whole screen would stop the Send of a chat whose message mentions "pay",
    # and a stop is terminal for a scripted case. The ONE op judged against
    # every element the packet carries is `press` (see `screen_hit`), because
    # the control its key reaches is not in the dump at all; the cost there is
    # one wasted turn, not a dead end, since the tap on the same screen's Send
    # is still allowed and the detail names the term so the model re-plans.
    "send money",
    "send payment",
    "send transfer",
    "send funds",
    "transfer",
    "withdraw",
    "reset",
    "deactivate",
    "close account",
    "sign out",
    "log out",
    "logout",
    "yes, delete",
)


# The lexicon term *text* matches as a whole word, or ``""``.
#
# Word-boundary matching is the entire point. ``"Deleted items"`` tokenises to
# ``["deleted", "items"]`` and matches nothing; ``"Confirm payment"`` matches
# ``confirm``. A multi-word entry is matched as a contiguous token run.
def destructive_hit(text: object) -> str:
    """The lexicon term *text* matches as a whole word, or ``""``."""
    segments = str(text or "").split(perception.SEGMENT_BREAK)
    if len(segments) > 1:
        # raw/split forms of one id: each judged alone, no cross-junction run.
        return next((hit for hit in map(destructive_hit, segments) if hit), "")
    tokens = _tokens(text)
    if not tokens:
        return ""
    for term in DESTRUCTIVE_LEXICON:
        wanted = _tokens(term)
        if not wanted:
            continue
        span = len(wanted)
        for start in range(0, len(tokens) - span + 1):
            if tokens[start : start + span] == wanted:
                return term
    return ""


def is_destructive(text: object) -> bool:
    return bool(destructive_hit(text))


def _script_moved(trace: object) -> bool:
    """Whether the script as a whole ended on another screen than it started on."""
    rows = [e for e in trace if isinstance(e, dict)] if isinstance(trace, list) else []
    first = str(rows[0].get("before_screen_id") or "") if rows else ""
    last = str(rows[-1].get("after_screen_id") or "") if rows else ""
    return bool(first and last and first != last)


# The ops in *trace* that ran, were meant to actuate, and moved nothing.
#
# In script order and DE-DUPLICATED, so three taps that each did nothing read
# as ``tap`` once rather than as a list the reader has to count.
#
# ``wait`` is excluded for the reason ``_settle`` does not mark it: a wait
# that changes nothing is the normal case, not a finding. Everything else in
# ``actions.MUTATING_OPS`` is here, because an op that was supposed to move
# the screen and did not is the same news whichever op it was.
def inert_ops(trace: object) -> list[str]:
    """The ops in *trace* that ran, were meant to actuate, and moved nothing."""
    out: list[str] = []
    if _script_moved(trace):
        return out
    for entry in trace if isinstance(trace, list) else []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("outcome") or "") != "no_change":
            continue
        action = entry.get("action")
        op = str((action or {}).get("op") or "") if isinstance(action, dict) else ""
        if not op or op in actions_mod.WAIT_OPS or op in NON_ACTUATING_OPS:
            continue
        if op not in out:
            out.append(op)
    return out


def _asserts_new_element(trace: list[dict]) -> bool:
    """True when THIS trace passed an assert naming a specific new thing.

    What a pass needs after a blocked turn. ``text_present`` does not count:
    the air session's pass stood on a greeting that was already on screen
    while the OTP it typed had moved nothing.
    """
    for item in trace:
        action = item.get("action") if isinstance(item, dict) else None
        if (
            str(item.get("outcome") or "") == "assert_pass"
            and isinstance(action, dict)
            and str(action.get("kind") or "") in NEW_ELEMENT_KINDS
        ):
            return True
    return False


def not_the_app_detail(package: object, expected: object) -> str:
    return (
        "the screen in front belongs to "
        + str(package or "another app")[:80]
        + ", not to "
        + str(expected or "the app under test")[:80]
        + ", so what this key would submit cannot be judged and nothing was "
        "sent. Bring the app back with `launch` -- or send `back` if a dialog "
        "is in front of it -- and then carry on in the same script."
    )


def _control_detail(hit: str) -> str:
    """The CONTROL stop's detail: :data:`GUARD_DETAIL` naming the term.

    One producer, because the confirm and its hint must recognise exactly
    what :func:`guard_detail` returns; comparing against the bare constant
    never matched, since the term is always appended.
    """
    return GUARD_DETAIL + " Matched: " + str(hit)
