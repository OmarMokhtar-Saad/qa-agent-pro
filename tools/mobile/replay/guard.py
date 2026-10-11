"""Replay engine: destructive-tap and screen-sentinel guards and their detail strings. Cut verbatim from tools/mobile/executor.py (P4.5)."""

from __future__ import annotations

import logging

from tools.mobile import actions as actions_mod
from tools.mobile import (
    perception,
)
from tools.mobile.providers import base as providers_base
from tools.mobile.replay.context import Context
from tools.mobile.replay.elements import _tokens, element_label

logger = logging.getLogger("tools.mobile.executor")


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

GUARD_DETAIL = (
    "Stopped before a control that looks irreversible. Nothing was tapped. "
    "Confirm with the tester, then resubmit the script with the same action."
)
#: What an UNCONFIRMED control stop adds, naming the field and the one op it
#: unlocks. Appended at the stop rather than folded into :data:`GUARD_DETAIL`,
#: whose wording the sentinel ratchet pins.
CONFIRM_HINT = (
    " After the tester confirms, resubmit with `confirm_destructive: true`: it "
    "unlocks this one `%s` on %s for that single submission and nothing else."
)
#: Bound on the guard-stop fingerprint's node text: untrusted device text,
#: written at a checkpoint and read back at a later submission, so it is
#: byte-compared across two separate device dumps.
MAX_GUARD_NODE_CHARS = 120
#: What a confirm that does not match the run's last recorded stop adds.
#: Same ``%(op, repr(hit[:60]))`` shape as :data:`CONFIRM_HINT`; the wording
#: names a DIFFERENT control so a test can grep for it.
CONFIRM_MISMATCH_HINT = (
    " That confirm was not spent: it named a different control than the one "
    "this run's last stop pointed at. Resubmit `confirm_destructive: true` "
    "against `%s` on %s -- the control the tester actually confirmed."
)
#: The rule the air session broke: it reported a login and two filled fields
#: that no observation showed. Carried by every blocked-type reply, and
#: restated in ``tools/guidance.py`` and both mobile tool docstrings.
NO_FABRICATION_NOTE = (
    "Never report a screen state, field value or login outcome that was not "
    "read from a qa_* observation. If the server cannot type or act, stop and "
    "report the blocker by name. Do not fall back to raw adb input, and do not "
    "claim a result."
)


#: The SAME stop, for a run whose charter says ``destructive: none``. A
#: SEPARATE sentence because both halves of :data:`GUARD_DETAIL` are wrong
#: here: there is no tester to confirm with and no resubmit that can succeed,
#: because the charter asked for a stop to END the attempt.
#:
#: This constant is the REFUSAL BY NAME the lane's rule requires. It changes
#: nothing about WHAT the guard stops: the term was produced by the lexicon
#: and by the actuated-node judging exactly as before, and this decides only
#: what happens AFTER a hit. It registers no sentinel and widens no lexicon.
GUARD_DETAIL_REFUSED = (
    "REFUSED: this run's charter says `destructive: none`, so a control that "
    "looks irreversible ENDS the attempt rather than pausing for the tester. "
    "Nothing was tapped and the run is over. A run started with "
    "`destructive: reversible` pauses for a confirmation instead."
)

#: The same stop for a screen the packet does not carry in full. A SEPARATE
#: sentence because both halves of :data:`GUARD_DETAIL` are false here: no
#: control was found -- none could be looked for -- and resubmitting the same
#: press re-truncates the same screen and stops again, forever. The remedy that
#: works is a tap, which is judged by the control's own label rather than by a
#: screen scan.
GUARD_DETAIL_NOT_FULLY_SEEN = (
    "Stopped before a press: this screen has more elements than one packet "
    "carries, so what the press would submit cannot be judged. Nothing was "
    "touched. Re-sending the same press stops here again -- tap the control "
    "this case means instead, which is judged by its own label."
)


#: The ONLY ops the destructive guard skips, because none can actuate anything:
#: an assert reads the screen, and ``done`` / ``ask_tester`` end the replay
#: before any device call. Everything else is guarded whether or not today's
#: implementation happens to touch the screen -- an allow-list of "ops that
#: tap" was wrong twice (``clear``, then a targeted ``scroll``), because the
#: guard is about the EFFECT and not the primitive.
#:
#: Adding an op here is a security decision and needs a stated reason. Pinned
#: behaviourally by the parametrised guard test in tests/mobile, which drives
#: every op in the vocabulary against a destructive control and fails on an op
#: it has never seen.
#: `clear_app_data` is deliberately NOT in this set: a whole-app wipe needs
#: the guard exactly like a tap or a type does, even though it names no
#: on-screen node. See `actions.action_text`'s `clear_app_data` branch, which
#: hands the guard a fixed destructive-lexicon phrase instead of an element
#: label -- there is no node to derive one from.
NON_ACTUATING_OPS: frozenset[str] = frozenset({"assert", "done", "ask_tester"})


def inert_ops(trace: object) -> list[str]:
    """The ops in *trace* that ran, were meant to actuate, and moved nothing.

    In script order and DE-DUPLICATED, so three taps that each did nothing read
    as ``tap`` once rather than as a list the reader has to count.

    ``wait`` is excluded for the reason ``_settle`` does not mark it: a wait
    that changes nothing is the normal case, not a finding. Everything else in
    ``actions.MUTATING_OPS`` is here, because an op that was supposed to move
    the screen and did not is the same news whichever op it was.
    """
    out: list[str] = []
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


#: Said when a script ran to the end and NOTHING it did moved the screen. The
#: FACT only: what the model should do instead is already stated once, in
#: ``actions.describe_vocabulary``'s note on `press`, and a second copy of that
#: advice here would be one meaning with two producers.
#:
#: IT LEADS THE REASON, and that placement is load-bearing rather than
#: stylistic: ``render.verdict_line`` truncates a reason to its own cap, and
#: this note appended sat behind roughly two hundred characters of boilerplate
#: and was cut off before any model saw it. Of the claims competing for that
#: budget, "nothing you did moved the screen" is the one a model can act on.
#: ``test_the_note_survives_the_verdict_line_cap`` is what holds it there.
NOTHING_MOVED_NOTE = (
    "Nothing on screen changed while this script ran (%s moved nothing), so "
    "whatever it was meant to trigger did not happen -- re-sending the same "
    "script will do the same thing again. "
)
#: The fixed opening of :data:`NOTHING_MOVED_NOTE`, which is what a stored
#: turn reason is matched on (the rest is formatted per turn).
NOTHING_MOVED_PREFIX = NOTHING_MOVED_NOTE.split(" (", 1)[0]


#: The ``guard_term`` a ``press`` gets on a screen the packet does not carry in
#: full. ``perception.prune`` caps the element list at ``MAX_ELEMENTS`` and
#: flags ``truncated``; a control below the cap is not in the packet, so "no
#: element on the screen is irreversible" cannot be said about that screen.
#: Measured by an executing review: a form with 200 filler rows above its
#: "Confirm payment" button pruned the button away, and a press on the amount
#: field sent KEYCODE_ENTER with the scan green.
#: A TERM, not a sentence, because it travels as one: ``build_tester_request``
#: caps ``guard_term`` at 60 characters -- a cap sized for "confirm" and "send
#: money" -- and the sentence this used to be reached the tester cut mid-word.
#: The sentence lives in :data:`GUARD_DETAIL_NOT_FULLY_SEEN`, which rides in
#: ``detail`` where there is room for it.
SCREEN_NOT_FULLY_SEEN = "screen not fully seen"

#: The guard term for a screen that belongs to ANOTHER APP. A separate sentinel
#: from the truncation one because the two need different sentences: the
#: truncation text says the packet holds only part of the screen and offers a
#: tap instead, and BOTH clauses are false here -- the packet carried this
#: screen whole, and tapping a control on the wrong app does not help.
#:
#: It is reachable without any earlier diagnosis: `open_url` and `home` are in
#: :data:`LEAVES_ON_PURPOSE`, so the left-app rule deliberately stays quiet on
#: them, and this refusal is then the only thing the tester is told.
SCREEN_NOT_THE_APP = "screen belongs to another app"

#: The guard term for an action landing on a control this packet did not read
#: in full -- a Flutter view with no semantics tree, or a region whose
#: identifying strings never reached the dump. A SEPARATE sentinel from the
#: truncation one because the CAUSE is different and so is the fix: the packet
#: carried this screen whole, and no re-plan of the script can recover a string
#: the app never exposed. Reusing the truncation wording would tell the tester
#: to do the wrong thing.
#:
#: THE BYPASS THIS CLOSES ONCE A PROVIDER EMITS REGIONS: a blind region gives
#: `actuated_element` one huge node, no lexicon term matches it, and an unknown
#: control is actuated with nothing stopping it -- while `truncated` stays
#: False, because nothing was truncated. INERT TODAY, deliberately:
#: `providers.composite.regions_of` returns no region in this step, so no live
#: element is ever stamped degraded or blind and this branch is reached only by
#: a test that supplies a region. It arms itself when steps 5 and 6 land the
#: Compose and Flutter readers -- nothing else has to change then.
SCREEN_CONTROL_NOT_READ = "control not read in full"


#: The sentence that rides in ``detail``, where there is room for the remedy.
#: Names the CAUSE-SPECIFIC fix: the app's own accessibility surface, not a
#: different tap and not a `launch`.
GUARD_DETAIL_CONTROL_NOT_READ = (
    "Stopped before acting: the control under that touch was not read in full "
    "-- the app does not expose its text to the accessibility layer this lane "
    "reads, so what the action would trigger cannot be judged. Nothing was "
    "touched. Turn accessibility on for the app under test, or use an "
    "instrumented build, and then tap the control again."
)


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


#: The generic screen-level stop, for a ``SCREEN_*`` sentinel that reached a
#: consumer with no entry of its own. Unreachable while the ratchet is green --
#: it exists because the SAFE answer to "a sentinel nobody registered" is a true
#: sentence about the screen, not :data:`GUARD_DETAIL`, whose two claims (a
#: control was found, re-sending may work) are false for every screen stop.
GUARD_DETAIL_SCREEN_GENERIC = (
    "Stopped before a press: this screen cannot be vouched for, so what the "
    "press would submit cannot be judged. Nothing was touched. Re-sending the "
    "same press stops here again -- read the trace for what was seen, and plan "
    "a different action."
)


def screen_sentinels() -> frozenset:
    """Every screen-level guard sentinel, FROM THE REGISTRY THAT HANDLES THEM.

    THE ONE DERIVATION. Both consumers and the ratchet that grades them read
    this rather than each deriving the set again -- mirrored conditions drift.

    It used to scan ``globals()`` for names starting with ``SCREEN_``, and a
    review refuted that in one mutation: the authority on what a screen-level
    stop IS is not the spelling of a module constant, it is what
    :func:`screen_hit` returns. A stop named ``PACKET_ROOT_DROPPED`` -- a
    failure mode this module's own comments describe as measured and real --
    was invisible to the scan, so it reached the model with the control wording
    at BOTH consumers while 1813 tests stayed green.

    Registration is now the definition: a sentinel exists here because a
    handler exists for it. What keeps the PRODUCER honest is a separate,
    mechanical check -- ``test_every_screen_level_stop_screen_hit_can_return_
    is_registered`` reads the returns out of :func:`screen_hit` itself, so a
    stop with no row fails at authoring time whatever it is called.
    """
    return frozenset(screen_stop_details())


#: Sentinel -> the ``detail`` that stop carries, as ``(screen, expected)``.
#: A REGISTRY, not an if/elif chain: the chain's ``else`` is what silently gave
#: a new sentinel the control wording, and an unregistered key is visible here
#: (``screen_stop_details()`` vs :func:`screen_sentinels`) where a missing
#: branch in a chain is not. Adding a sentinel without a row fails the ratchet.
def screen_stop_details() -> dict:
    return {
        SCREEN_NOT_FULLY_SEEN: lambda screen, expected: GUARD_DETAIL_NOT_FULLY_SEEN,
        SCREEN_NOT_THE_APP: lambda screen, expected: not_the_app_detail(
            (screen or {}).get("package"), expected
        ),
        SCREEN_CONTROL_NOT_READ: lambda screen, expected: GUARD_DETAIL_CONTROL_NOT_READ,
    }


def _control_detail(hit: str) -> str:
    """The CONTROL stop's detail: :data:`GUARD_DETAIL` naming the term.

    One producer, because the confirm and its hint must recognise exactly
    what :func:`guard_detail` returns; comparing against the bare constant
    never matched, since the term is always appended.
    """
    return GUARD_DETAIL + " Matched: " + str(hit)


def guard_detail(hit: str, screen: object, expected: object) -> str:
    """The ``detail`` for ANY guard stop -- the ONE place that decides.

    A registered sentinel gets its own sentence. A term that IS a lexicon
    entry really is a control, so it keeps :data:`GUARD_DETAIL`. ANYTHING ELSE
    -- including an unregistered screen stop, whatever it is named and however
    it is phrased -- gets the generic screen sentence and an error log: saying
    something true and unhelpful beats saying something false and actionable.

    The middle test is IDENTITY with the lexicon, not containment; the
    containment form let a stop phrased "the app was reset between actions"
    read as a control. Identity needs the EMPTY guard explicitly, and the
    switch to it did not have one: ``destructive_hit("") != ""`` is False, so
    an empty term fell through to the control wording -- "a control that looks
    irreversible ()" -- where containment had given the generic sentence. A
    fail-safe whose degenerate input fails OPEN is the wrong way round, and
    this docstring claimed the opposite in the same commit that broke it. Never raises -- this is on the replay path, and a guard
    that crashes is a guard that is off.
    """
    try:
        handler = screen_stop_details().get(hit)
        if handler is not None:
            return handler(screen if isinstance(screen, dict) else {}, expected)
        if not hit or destructive_hit(hit) != hit:
            return _unrecognised_guard_term(hit)
    except Exception:  # pragma: no cover - defensive; the guard must not crash
        logger.error("mobile.executor.guard_detail failed", exc_info=True)
        return GUARD_DETAIL_SCREEN_GENERIC
    return _control_detail(hit)


def _unrecognised_guard_term(hit: str) -> str:
    """Log a guard term that is neither a screen stop nor a lexicon match."""
    # NOT "is it a known sentinel" -- that question cannot be asked
    # honestly here, because registration now DEFINES the sentinel set,
    # so the answer is yes exactly when a handler was found above.
    # The question that survives is the one GUARD_DETAIL actually
    # claims: is this a CONTROL? A term the lexicon does not match is
    # not one, whatever it is called, so the control wording would be
    # false and the generic screen sentence is what is true.
    #
    # IDENTITY, not containment. `not destructive_hit(hit)` was the
    # first form and a review broke it in one line: a stop phrased "the
    # app was reset between actions" CONTAINS the lexicon token "reset",
    # so it read as a control and got the control wording. What arrives
    # here from the guard is the lexicon TERM itself (see the call site
    # -- `hit = destructive_hit(label)`), so a genuine term is its own
    # match and a sentence that merely mentions one is not.
    # This is the runtime half of the producer check in the ratchet: a
    # screen-level stop nobody registered fails safe HERE even if it
    # reached a shipped build.
    # ERROR, not warning: a guard quietly degrading to a generic
    # sentence is the exact failure mode this whole chain is about, and
    # a warning in a replay log is a line nobody reads.
    logger.error(
        "mobile.executor: guard term %r is neither a registered screen "
        "stop nor a lexicon match",
        hit,
    )
    return GUARD_DETAIL_SCREEN_GENERIC


# Notes on screen_hit's branches (kept here so the function stays short).
#
# THE FIDELITY QUESTION, and ONLY it. `judged` PRESENT selects the first branch
# and returns; ABSENT leaves everything after it byte-for-byte what it was. It
# is a parameter rather than a second function on purpose: the ratchet
# (test_screen_hit_is_the_only_screen_level_producer) reads the producers out
# of this module and a new one would be ungraded.
#
# DELEGATED, never re-derived. `providers_base.is_untrusted` is THE one answer
# to "may the guard judge what it read here", and it carries its own non-dict
# check, so spelling the lookup again would be the same answer derived twice
# from the same input -- mirrored conditions drift. ABSENT or UNKNOWN is NOT
# untrusted -- only `degraded` and `blind` are; a guard stop is terminal for a
# scripted case, so refusing on absence would stop every checkpoint written
# before the seam existed. That rule lives in `providers_base`, once.
#
# TRUNCATION IS ONE WAY THE PACKET CAN FAIL TO CARRY THE SCREEN, NOT THE ONLY
# ONE. `prune` takes its root bounds from the first node carrying them and
# drops everything outside; on a multi-window dump whose systemui window sorts
# first, the app's whole window is dropped and `truncated` stays False.
# Measured: one element kept (a clock), and the press guard cleared a screen
# holding "Confirm payment" that it had never seen. So a screen whose dominant
# package is not the app under test cannot be vouched for. An EMPTY
# `expected_package` means the caller did not say which app it is driving, and
# a refusal invented on no evidence would be its own defect -- so that case
# keeps today's answer.
def screen_hit(
    screen: object, expected_package: str = "", judged: object = None
) -> str:
    """The lexicon term some element on *screen* carries in its OWN strings, a
    ``SCREEN_*`` sentinel, or ``""``.

    The sentinels are :data:`SCREEN_NOT_FULLY_SEEN`, when the packet is
    truncated, and :data:`SCREEN_NOT_THE_APP`, when the dominant package is not
    the app under test. Both are guard TERMS that travel on to the model, and
    each needs its own branch in every consumer that reads one -- pinned by
    ``tests/mobile/test_mobile_sentinel_ratchet.py``, which derives the list
    from this module. This docstring is a claim; that file is the check.

    What a ``press`` is judged against, because the control its key reaches is
    not in the dump. Judged ELEMENT BY ELEMENT, never as one joined string: a
    multi-word lexicon term matches a contiguous token run, and a join put
    ``CallLog`` beside "out of office" and read "log out" across the seam --
    the same false stop the actuated-node path avoids. Each element
    contributes ``element_label`` WITHOUT containment; a wrapper's contained
    text belongs to other elements, which are judged on their own turn. Never
    raises.
    """
    # Notes on each branch: the comment block above this function.
    if judged is not None:
        if providers_base.is_untrusted(judged):
            return SCREEN_CONTROL_NOT_READ
        return ""
    if not isinstance(screen, dict):
        return ""
    if screen.get("truncated"):
        return SCREEN_NOT_FULLY_SEEN
    seen = str(screen.get("package") or "")
    if expected_package and seen and seen != str(expected_package):
        return SCREEN_NOT_THE_APP
    for element in screen.get("elements") or []:
        hit = destructive_hit(element_label(element))
        if hit:
            return hit
    return ""


def destructive_hit(text: object) -> str:
    """The lexicon term *text* matches as a whole word, or ``""``.

    Word-boundary matching is the entire point. ``"Deleted items"`` tokenises to
    ``["deleted", "items"]`` and matches nothing; ``"Confirm payment"`` matches
    ``confirm``. A multi-word entry is matched as a contiguous token run.
    """
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


#: Assert kinds that name something the screen now SHOWS as a thing: an
#: element, or text that was not there before this turn's action.
NEW_ELEMENT_KINDS = ("element", "new_text")


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


def _consume_confirm(
    ctx: Context,
    hit: str,
    screen: object,
    entry: dict,
    op_label: tuple[str, str],
) -> bool:
    """Spend the one-shot ``confirm_destructive`` on THIS control stop, or not.

    Only a CONTROL stop (the :data:`GUARD_DETAIL` wording) can be confirmed; a
    screen-level stop names a different move, and ``guard_refuses`` outranks
    any confirm. Bound to the op the guard stopped on the PRIOR turn, not any
    control: ``ctx.prior_guard_stop`` carries that stop's fingerprint
    (``None`` means untracked, the lenient default every direct
    ``Context(...)`` unit test still gets; ``{}`` means tracked and no stop
    was recorded). A tracked confirm that does not match refuses by name and
    leaves the confirm UNSPENT, so a later, correctly-aimed resubmission can
    still use it. The trace entry records what was confirmed, so the run
    folder's checkpoint of this turn names the op and the term.

    *op_label* is ``(op, label)``: the op that stopped and the control's label.
    """
    op, label = op_label
    if not ctx.confirm_destructive or bool(getattr(ctx, "guard_refuses", False)):
        return False
    if guard_detail(hit, screen, ctx.package) != _control_detail(hit):
        return False
    stopped = getattr(ctx, "prior_guard_stop", None)
    if stopped is not None:
        current = {
            "term": str(hit)[:80],
            "op": str(op)[:40],
            "node": _guard_node(label),
        }
        if not stopped or stopped != current:
            entry["guard_confirm_mismatch"] = True
            return False
    ctx.confirm_destructive = False
    entry["guard_confirmed"] = {"op": op, "term": str(hit)[:80]}
    return True


def _guard_node(label: object) -> str:
    """The stopped node as the confirm fingerprint and the host see it.

    ``SEGMENT_BREAK`` is an internal seam, so it leaves as a space: the host
    never gets a control character, and a stop fingerprinted before the seam
    existed still matches.
    """
    return str(label).replace(perception.SEGMENT_BREAK, " ")[:MAX_GUARD_NODE_CHARS]


def _with_confirm_hint(detail: str, op: str, hit: str, mismatched: bool = False) -> str:
    """:data:`CONFIRM_HINT` on a control stop; any other stop is unchanged."""
    if detail != _control_detail(hit):
        return detail
    hint = CONFIRM_MISMATCH_HINT if mismatched else CONFIRM_HINT
    return detail + hint % (op, repr(str(hit)[:60]))


def _confirmed(entry: dict, detail: str) -> str:
    """Prefix the detail of an op that ran on a confirmed destructive stop."""
    confirmed = entry.get("guard_confirmed")
    if not isinstance(confirmed, dict):
        return detail
    return (
        "CONFIRMED destructive op ("
        + str(confirmed.get("op") or "")
        + " on "
        + repr(str(confirmed.get("term") or ""))
        + "): "
        + detail
    ).strip()
