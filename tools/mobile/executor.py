"""Replay one validated script against a device, and stop honestly.

Four exits, and which one you get is the whole design:

* ``done``          -- the script ran out or reached ``done``.
* ``needs_model``   -- a target was not on the screen, or an assert failed. The
  trace and the NEW screen go back to the tester's model, which continues the
  script from there. This is the escape hatch, not a failure.
* ``needs_tester``  -- an ``ask_tester`` action, or the destructive guard stopped
  in front of a confirm/delete/pay control.
* ``error``         -- the DEVICE failed (adb gone, dump unparseable). The one
  case where nothing useful can be asked of anybody.

Secrets: a ``type`` action carrying ``secret: true`` is replayed through
``ime.type_text``, whose payload rides on stdin and never becomes an argv
element (Phase 1 proved the transport). This module owes the other half -- the
RECORD. Every trace entry stores ``actions.redact_action(...)``, so the value is
``***`` in the returned trace, in the checkpoint written from it, and in
anything a handler builds out of it.

The destructive guard matches WHOLE WORDS, not substrings. "Confirm" stops;
"Deleted items" does not, because its token is ``deleted`` and the lexicon holds
``delete``. A guard that stops everything is exactly as broken as one that stops
nothing, which is why both directions are pinned.
"""

from __future__ import annotations

import dataclasses
import logging
import re
import time

from tools.mobile import actions as actions_mod
from tools.mobile import adb, ime, perception

logger = logging.getLogger(__name__)

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
    # "Send" is not stopped by this lexicon. Widening the guard to the whole
    # screen was considered and refused -- a chat message mentioning "pay"
    # would then stop the send, and the tester-request packet cannot get past
    # this guard, so the case would dead-end. That was the defect the bare
    # token caused, and trading it back for a broader net is not a fix.
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

#: Packages whose screen is an OVERLAY: the app under test is still running
#: underneath, so `back` dismisses this and the case continues. A permission
#: dialog is the one a mistaken tap produces most often -- on 2026-09-04 a tap
#: on a Voice mode control opened the microphone prompt, and the replay went on
#: tapping against it until the case ran out of escapes.
#: Re-exported from ``perception``, which is where the answer has to be taken:
#: the overlay's package is gone from the pruned elements by the time this
#: module sees a screen, because it is the same on every element of an ordinary
#: one. The set has ONE definition and this name is kept for its readers.
SYSTEM_DIALOG_PACKAGES: frozenset = perception.SYSTEM_DIALOG_PACKAGES

#: Launchers, named ONLY so the reason can say "the home screen" rather than a
#: package name a tester would have to look up. The left-the-app rule itself is
#: "a package that is not the one under test" and does not consult this set --
#: a list of launchers would be a list to keep up to date, and the rule must
#: hold for the ones nobody thought of.
LAUNCHER_PACKAGES: frozenset = frozenset(
    {
        "com.google.android.apps.nexuslauncher",
        "com.android.launcher",
        "com.android.launcher2",
        "com.android.launcher3",
        "com.sec.android.app.launcher",
    }
)

#: Ops that leave the app ON PURPOSE. Reporting one as an accident would stop a
#: script doing exactly what it asked to do.
LEAVES_ON_PURPOSE: frozenset = frozenset({"home", "open_url"})

MAX_TRACE = 80
_TOKEN_RE = re.compile(r"[a-z0-9]+")

STATUS_DONE = "done"
STATUS_NEEDS_MODEL = "needs_model"
STATUS_NEEDS_TESTER = "needs_tester"
STATUS_ERROR = "error"

#: A "done" carrying verdict=pass that recorded no assert_pass and no screen
#: change is not evidence of anything -- see ``_has_verification``.
STATUS_UNVERIFIED = "unverified"

#: How often ``wait`` with ``until_text`` re-dumps the screen while polling.
WAIT_POLL_S = 1.0

#: The bound for ``wait`` with ``until_text`` and no explicit ``ms`` -- keeps a
#: script that forgot to set ms from polling forever.
DEFAULT_WAIT_UNTIL_TEXT_S = 20.0

#: The wall clock ONE replay may spend, derived from the vocabulary's own
#: number so the packet and the enforcement cannot disagree. Checked BEFORE an
#: action starts and never inside one, so a stop never leaves half a tap behind:
#: the trace records what ran, the packet carries the current screen, and the
#: model continues from there exactly as it would after any other escape.
SUBMIT_BUDGET_S = actions_mod.SUBMIT_BUDGET_MS / 1000.0


@dataclasses.dataclass
class Context:
    """Everything a replay needs about the device it is driving."""

    serial: str
    package: str = ""
    activity: str = ""
    guard_destructive: bool = True
    screen: dict | None = None
    # One tester-supplied value per field, held for THIS call only and never
    # written anywhere. ``case_runner`` fills it from the submit argument.
    tester_inputs: dict | None = None
    # Per-call wall-clock bound for the replay. ``None`` means
    # :data:`SUBMIT_BUDGET_S`; a test shortens it rather than sleeping.
    budget_s: float | None = None


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
NON_ACTUATING_OPS: frozenset[str] = frozenset({"assert", "done", "ask_tester"})


def _tokens(text: object) -> list[str]:
    return _TOKEN_RE.findall(str(text or "").lower())


#: Every string-valued key a pruned element carries. The guard reads ALL of
#: them, and a completeness test asserts this set still matches perception's
#: output -- so adding a field there forces a decision here instead of silently
#: narrowing the evidence. That ratchet is the point: this guard was walked past
#: three times by evidence it did not read.
IDENTIFYING_KEYS: tuple[str, ...] = ("text", "desc", "rid", "cls")


def _split_camel(text: str) -> str:
    """``DeleteAccountButton`` -> ``Delete Account Button``. Delegates.

    The implementation moved to ``perception.split_camel`` when the role
    lexicon needed the same question asked of the same strings: ONE tokenising
    idiom in this package, not three private copies that drift. The name and
    the docstring below are kept because the guard's reasoning lives here.

    Applied to the CLASS name alone. The lexicon matches whole tokens, which is
    what keeps "Deleted items" from being a false positive -- but it also means
    a camel-case run is one token, so ``DeleteAccountButton`` lowercased to
    ``deleteaccountbutton`` never matched ``delete`` and a custom widget class
    walked past the guard. Text and desc are human-written and already
    word-separated, so they are left exactly as the app wrote them.

    Does not widen the lexicon: ``ConfirmationTextView`` becomes
    ``Confirmation Text View`` and ``confirmation`` is still not ``confirm``.
    """
    return perception.split_camel(text)


def element_label(element: object, screen: object = None) -> str:
    """Everything about *element* a destructive lexicon should judge.

    The guard read ``text`` and ``desc`` only, and four routes walked past it,
    each confirmed by execution:

    * the word only in the RESOURCE-ID -- a button showing "Continue" whose id
      is ``id/delete_account``. For an icon-only control the rid is usually the
      ONLY evidence there is, which is why dropping it mattered most.
    * the word only in the CLASS name, e.g. a custom ``DeleteAccountButton``.
    * the word only in a CHILD node: a clickable row whose own text is empty,
      wrapping a label reading "Delete account". That is the ordinary Android
      list-item shape, not an exotic one.
    * the same element selected by ``id`` rather than ``rid``, which changed
      the verdict for one control, because the ACTION's text contributed the rid
      and the element's label did not.

    Containment is consulted ONLY for an element with no label of its own. A
    labelled button is judged on its label; a bare wrapper is judged on what it
    visibly contains. Reading descendants unconditionally would let a
    screen-sized container inherit every word on screen and stop ordinary taps.

    THE RESIDUAL, stated rather than implied: a control carrying no identifying
    string at all -- empty text, empty desc, no rid, a generic class -- cannot
    be classified by any lexicon, and this does not change that. The guard is a
    strong default, never a proof.
    """
    if not isinstance(element, dict):
        return ""
    parts = [
        _split_camel(str(element.get(key) or ""))
        if key == "cls"
        else str(element.get(key) or "")
        for key in IDENTIFYING_KEYS
    ]
    if element.get("text") or element.get("desc"):
        return " ".join(p for p in parts if p).strip()
    parts.extend(_contained_text(element, screen))
    return " ".join(p for p in parts if p).strip()


def _contained_text(element: dict, screen: object) -> list[str]:
    """Text of the elements drawn INSIDE *element*.

    Only reached for an element with no text or desc of its own. Bounds are the
    only containment signal a pruned screen carries -- the tree shape is gone by
    design -- so "inside" means strictly smaller and fully within, and an
    element is never its own child.
    """
    if not isinstance(screen, dict):
        return []
    box = element.get("bounds")
    if not (isinstance(box, (list, tuple)) and len(box) == 4):
        return []
    try:
        x1, y1, x2, y2 = (int(v) for v in box)
    except (TypeError, ValueError):
        return []
    own_area = (x2 - x1) * (y2 - y1)
    out: list[str] = []
    for other in screen.get("elements") or []:
        if not isinstance(other, dict) or other is element:
            continue
        if other.get("id") and other.get("id") == element.get("id"):
            continue
        inner = other.get("bounds")
        if not (isinstance(inner, (list, tuple)) and len(inner) == 4):
            continue
        try:
            a1, b1, a2, b2 = (int(v) for v in inner)
        except (TypeError, ValueError):
            continue
        if a1 < x1 or b1 < y1 or a2 > x2 or b2 > y2:
            continue
        if (a2 - a1) * (b2 - b1) >= own_area:
            continue
        for key in ("text", "desc"):
            value = str(other.get(key) or "")
            if value:
                out.append(value)
    return out


def destructive_hit(text: object) -> str:
    """The lexicon term *text* matches as a whole word, or ``""``.

    Word-boundary matching is the entire point. ``"Deleted items"`` tokenises to
    ``["deleted", "items"]`` and matches nothing; ``"Confirm payment"`` matches
    ``confirm``. A multi-word entry is matched as a contiguous token run.
    """
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


async def _dump(ctx: Context) -> dict:
    """Re-dump and re-prune. Returns the ``{"error","content"}`` shape."""
    raw = await adb.uiautomator_dump(ctx.serial)
    if raw.get("error"):
        return raw
    return perception.prune(raw.get("content"), ctx.activity)


#: The wall clock, bound at import. Tests replace `executor.time` with a fake
#: that carries only `monotonic` to drive the duration stamp; the absolute `at`
#: below must keep reading the REAL clock under that fake, and must stay the
#: same clock `run_store` stamps `started`/`updated` with.
_wall_clock = time.time


def _entry(index: int, action: object, before: str, started: float) -> dict:
    return {
        "index": int(index),
        "action": actions_mod.redact_action(action),
        "before_screen_id": str(before or ""),
        "after_screen_id": "",
        # Stamped by ``_append``, AFTER the action ran. Computing it here read
        # the clock at creation, so every real run carried ``ms: 0`` -- a
        # 4000 ms wait included -- and the report could time nothing.
        "ms": 0,
        "_started": float(started),
        # Host wall clock at the moment the action BEGAN, the same clock the
        # case checkpoint's `started`/`updated` use, so app-log evidence can be
        # placed between actions by absolute time (plan mobile-app-evidence D6).
        "at": _wall_clock(),
        "outcome": "",
        "detail": "",
    }


#: What a refused `type` into a password input comes back as. NEEDS_MODEL, not
#: ERROR: the refusal tells the model to ask the tester with `ask_tester` and
#: then reference the field, and an ERROR ends the case -- so the instruction
#: could not be followed by the only reader it has. A target that is not on the
#: screen, a lesser mistake, has always got a re-plan.
PASSWORD_REFUSAL_STATUS = STATUS_NEEDS_MODEL

PASSWORD_REFUSAL = (
    "That field is a password input, so nothing was typed into it. Ask the "
    "tester for the value with ask_tester, then reference that same field name "
    "from a type action with secret=true and no text -- the value is typed "
    "straight to the device and is never stored."
)


#: The AMBIGUOUS-ROLE message. A formatter rather than a constant because the
#: count is the useful part of it, and ``test_every_miss_message_is_reachable``
#: enumerates it alongside the constants so it cannot become unreachable
#: without saying so.
AMBIGUOUS_ROLE_PREFIX = "That role matched "


def ambiguous_role_detail(count: object) -> str:
    """Their message, kept verbatim: the role was SEEN and not narrowed."""
    return (
        AMBIGUOUS_ROLE_PREFIX
        + str(int(count))
        + " tappable controls on this screen, so the replay stopped rather "
        "than guessing which you meant. Name one of them by its label, its "
        "rid or its short id."
    )


def missing_element_detail(resolved: object) -> str:
    """Why a target resolved to nothing, in the model's own terms.

    Five answers, because they need five different next moves, and a model told
    only "no element matched" re-plans the same way it planned the first time:

    * an AMBIGUOUS role -- the role was SEEN and could not be narrowed, so the
      advice is to name one of them, not to look for something absent;
    * selectors that name DIFFERENT elements -- a stale plan, and picking
      either would be guessing which half to trust;
    * a selector that matched nothing while another matched -- also a stale
      plan, and the remedy names what to switch to;
    * an ``id`` alone that matched nothing -- the commonest miss there is, and
      the one whose remedy (`rid`/text) is worth stating;
    * nothing matched at all -- the planner named something that is not here.

    The ambiguity branch comes FIRST: a role that matched two tappable controls
    has been seen, and saying "nothing matched" about it would be false.
    """
    body = resolved if isinstance(resolved, dict) else {}
    try:
        candidates = int(body.get("candidates") or 0)
    except (TypeError, ValueError):
        candidates = 0
    if candidates > 1:
        return ambiguous_role_detail(candidates)
    if body.get("conflict"):
        return MISS_CONFLICT
    if body.get("stale"):
        # WHICH message depends on whether anything still matched, not on how
        # many selectors were supplied. The cross-selector wording says "one
        # selector matches nothing while another one does" -- true only when
        # something did.
        supplied = tuple(body.get("supplied") or ())
        stale_selectors = tuple(body.get("stale_selectors") or ())
        if len(supplied) > len(stale_selectors):
            return MISS_STALE_SELECTOR
        # Nothing matched. The id-specific remedy ("prefer `rid` or the exact
        # on-screen text") is only USEFUL when the id was the only selector
        # supplied; where a rid or a text missed too, it names what just failed.
        if len(supplied) == 1:
            return MISS_STALE_ID
        return MISS_STALE_ALL
    return MISS_PLAIN


def system_dialog_package(screen: object) -> str:
    """The system-dialog package on this screen, or ``""``.

    ANY element's, not the dominant one's. A permission prompt is a CARD over a
    full-screen app window, so it loses on area every time -- and keying the
    dialog rule on the screen's dominant package made it inert for exactly the
    packages it exists for. The dominant package answers a different question
    ("whose screen is this") and still answers it for the left-the-app rule.
    """
    if not isinstance(screen, dict):
        return ""
    named = str(screen.get("dialog_package") or "")
    if named in SYSTEM_DIALOG_PACKAGES:
        return named
    # The whole screen IS the dialog -- a full-screen permission prompt, or a
    # dump that held nothing else.
    whole = str(screen.get("package") or "")
    return whole if whole in SYSTEM_DIALOG_PACKAGES else ""


def _screen_package(screen: object) -> str:
    return str((screen or {}).get("package") or "") if isinstance(screen, dict) else ""


def system_dialog_detail(package: str) -> str:
    return (
        "a system dialog from "
        + str(package)[:80]
        + " is in front of the app, so nothing further was replayed against it. "
        "The app is still running underneath: answer the dialog, or send `back` "
        "to dismiss it, then continue the case."
    )


#: Windows that belong to the platform rather than to an app: an ANR ("X isn't
#: responding"), a crash dialog. They are `package="android"` and they are
#: DOMINANT, so they reach the left-app branch rather than the dialog one --
#: and `android` deliberately does not join SYSTEM_DIALOG_PACKAGES, because it
#: also appears on ordinary screens and the membership rule that keeps that set
#: safe is worth more than this one case.
#:
#: What has to change instead is the ADVICE. `launch` cannot clear an ANR.
PLATFORM_PACKAGES: frozenset = frozenset({"android"})


def left_app_detail(package: str, expected: str) -> str:
    if str(package) in PLATFORM_PACKAGES:
        return (
            "the screen now belongs to the Android platform ("
            + str(package)[:80]
            + ") rather than to "
            + str(expected)[:80]
            + ' -- this is usually a system dialog such as "isn\'t responding" '
            "in front of the app, so nothing further was replayed. Answer it "
            "using the buttons on the screen above, or send `back`. Do NOT send "
            "`launch`: it does not clear a dialog like this."
        )
    where = (
        "the home screen (" + str(package)[:80] + ")"
        if str(package) in LAUNCHER_PACKAGES
        else "another app (" + str(package)[:80] + ")"
    )
    return (
        "the screen now belongs to "
        + where
        + " and not to "
        + str(expected)[:80]
        + ", so nothing further was replayed. Send `launch` to bring the app "
        "under test back to the front, then continue the case."
    )


def _screen_id(screen: object) -> str:
    return (
        str((screen or {}).get("screen_id") or "") if isinstance(screen, dict) else ""
    )


def _screen_has(screen: object, needle: str) -> bool:
    want = " ".join(str(needle or "").split()).lower()
    if not want or not isinstance(screen, dict):
        return False
    for element in screen.get("elements") or []:
        if not isinstance(element, dict):
            continue
        for field in ("text", "desc"):
            if want in " ".join(str(element.get(field) or "").split()).lower():
                return True
    return False


def _texts(screen: object) -> set:
    """Every visible string on *screen*, whitespace-normalised.

    The unit ``new_text`` diffs. Element ids are positional and change on every
    dump, and bounds move when a list scrolls, so CONTENT is the only stable
    thing to compare two screens by.
    """
    out: set = set()
    if not isinstance(screen, dict):
        return out
    for element in screen.get("elements") or []:
        if not isinstance(element, dict):
            continue
        for field in ("text", "desc"):
            value = " ".join(str(element.get(field) or "").split())
            if value:
                out.add(value)
    return out


_DIGITS_RE = re.compile(r"\d")


def _is_furniture(text: object) -> bool:
    """True for a bare clock, counter or badge -- a number and its punctuation.

    The digit-shape rule is bounded by this, and the bound is the whole of it:
    without one, "Your appointment is 10:30" -> "Your appointment is 14:00" and
    "Order 1" -> "Order 2" were both dropped as furniture, so an app answering
    with a DIFFERENT NUMBER -- the most likely true positive an appointments
    app has -- reported that nothing new appeared.

    A string carrying a letter is something someone wrote. A string that is
    only digits and separators is something that ticks.
    """
    return not any(character.isalpha() for character in str(text or ""))


def _digit_shape(text: object) -> str:
    """``10:43`` -> ``##:##``. Two strings with the same shape are the same
    piece of furniture showing a different number."""
    return _DIGITS_RE.sub("#", str(text or ""))


def _typed_by_script(trace: list[dict]) -> set:
    """Every literal this script has typed so far.

    The app putting your own words back on the screen is not the app
    answering, and a chat thread does exactly that.
    """
    out: set = set()
    for item in trace or []:
        action = item.get("action")
        if not isinstance(action, dict) or str(action.get("op") or "") != "type":
            continue
        value = " ".join(str(action.get("text") or "").split())
        if value:
            out.add(value)
    return out


def _verification_summary(trace: list[dict]) -> str:
    """What this trace actually VERIFIED, in the asserts' own words.

    A pass whose reason describes an omission ("the script finished without a
    done() action") tells a tester nothing about the app. The asserts already
    recorded what they checked; this joins them so the verdict can say it.
    """
    parts: list[str] = []
    for item in trace:
        if str(item.get("outcome") or "") != "assert_pass":
            continue
        action = item.get("action")
        kind = str(action.get("kind") or "") if isinstance(action, dict) else ""
        detail = str(item.get("detail") or "")
        line = (kind + " -- " + detail) if (kind and detail) else (kind or detail)
        if line:
            parts.append(line)
    return "; ".join(parts)[:600]


def _has_verification(trace: list[dict]) -> bool:
    """True when *trace* holds evidence a ``done verdict=pass`` can stand on.

    ONE thing counts: an ``assert_pass``. Something has to have been CHECKED.

    **2026-09-04 -- a screen change used to count too, and that was wrong.**
    The rule read "an ``assert_pass``, or a mutating op whose screen changed",
    on the reasoning that a screen that moved is evidence the app did
    something. It is evidence of ACTIVITY, not of verification, and any
    navigation satisfies it. Measured on the released build, both of these
    were recorded as ``pass``:

    * ``mrun-20260904-181558-ae196e`` -- a single ``tap``. One op, no assert.
    * ``mrun-20260904-181254-98dfbe`` -- ``[tap, back, wait]``. It pressed
      BACK and passed.

    Neither did the work its case described (send two Arabic questions, check
    the assistant answered), and a tester reading either report would have
    been told the app behaved. That is worse than no report. The original D3
    defect was ``[wait, done pass]``; closing it while leaving this branch
    open just moved the bar from "nothing at all" to "any tap".

    The cost is deliberate: a script that drives the app correctly but asserts
    nothing is now ``unverified`` rather than ``pass``. That is the honest
    answer -- nothing was verified -- and the vocabulary already carries
    ``assert`` for saying so.
    """
    return any(str(item.get("outcome") or "") == "assert_pass" for item in trace)


async def _settle(
    ctx: Context,
    entry: dict,
    screen: object,
    trace: list[dict],
    index: int,
    *,
    redump: bool,
    mark_no_change: bool,
    op: str = "",
) -> tuple:
    """Re-read the screen after an action that could have changed it.

    THE ONE settle path, and it exists because there used to be none for
    ``wait``. ``wait`` is in ``actions.MUTATING_OPS``, but its branch returned
    before the re-dump that membership implied -- so a plain ``wait ms`` NEVER
    re-read the screen, and every assert after a wait was evaluated against the
    PRE-wait screen. An app's reply arrives during exactly that wait. It is why
    ``screen_changed`` was the only kind that appeared to work on the
    2026-09-04 live run: it compares ids rather than content.

    Returns ``(screen, stop)``. ``stop`` is a complete replay result when the
    replay must end here (and this function has already appended the entry),
    and ``None`` when the caller carries on and appends it itself.

    *mark_no_change* is False for ``wait``: ``no_change`` exists to tell a model
    "your tap did nothing", and a wait that changes nothing is the normal case.
    """
    before_id = str(entry.get("before_screen_id") or "")
    if redump:
        dumped = await _dump(ctx)
        if dumped.get("error"):
            entry["outcome"] = "dump_failed"
            entry["detail"] = str(dumped["error"])[:400]
            _append(trace, entry)
            return screen, _result(
                STATUS_ERROR, trace, screen, "", entry["detail"], index
            )
        screen = dumped.get("content")
    if mark_no_change and _screen_id(screen) == before_id:
        entry["outcome"] = "no_change"

    # The app is not where the script thinks it is. Recorded on the ACTION that
    # took it there, and the replay stops: every target after this one would
    # miss against a screen that belongs to somebody else, which is how one
    # mistaken tap spent a case's three escapes and reported `blocked` without
    # ever mentioning that the app had been left.
    package = _screen_package(screen)
    dialog = system_dialog_package(screen)
    if dialog:
        entry["outcome"] = "system_dialog"
        entry["detail"] = system_dialog_detail(dialog)
        entry["after_screen_id"] = _screen_id(screen)
        _append(trace, entry)
        return screen, _result(
            STATUS_NEEDS_MODEL, trace, screen, "", entry["detail"], index
        )
    if (
        package
        and ctx.package
        and package != ctx.package
        and str(op or "") not in LEAVES_ON_PURPOSE
    ):
        entry["outcome"] = "left_app"
        entry["detail"] = left_app_detail(package, ctx.package)
        entry["after_screen_id"] = _screen_id(screen)
        _append(trace, entry)
        return screen, _result(
            STATUS_NEEDS_MODEL, trace, screen, "", entry["detail"], index
        )
    return screen, None


async def _wait_until_text(
    ctx: Context, screen: object, text: str, ms: int
) -> tuple[bool, object, bool]:
    """Poll the screen until *text* appears or the budget runs out.

    Returns ``(found, latest_screen, timed_out)``. ``ms`` is the caller's cap;
    ``0`` falls back to :data:`DEFAULT_WAIT_UNTIL_TEXT_S` so a script that
    forgot to set it does not poll forever.
    """
    budget = (ms / 1000.0) if ms else DEFAULT_WAIT_UNTIL_TEXT_S
    deadline = time.monotonic() + budget
    current = screen
    while True:
        if _screen_has(current, text):
            return True, current, False
        if time.monotonic() >= deadline:
            return False, current, True
        await _sleep(WAIT_POLL_S)
        dumped = await _dump(ctx)
        if dumped.get("error"):
            return False, current, True
        current = dumped.get("content")


def _center(element: dict) -> tuple[int, int] | None:
    bounds = element.get("bounds") or []
    if len(bounds) != 4:
        return None
    try:
        x1, y1, x2, y2 = (int(v) for v in bounds)
    except (TypeError, ValueError):
        return None
    return (x1 + x2) // 2, (y1 + y2) // 2


_SWIPE = {
    "up": (540, 1500, 540, 500),
    "down": (540, 500, 540, 1500),
    "left": (900, 1000, 200, 1000),
    "right": (200, 1000, 900, 1000),
}


def _budget_seconds(ctx: Context) -> float:
    """This replay's wall-clock bound, always positive.

    A zero or negative budget would stop the FIRST action, so the replay would
    make no progress and every submit would spend an escape for nothing. An
    unusable value falls back to the module default rather than deadlocking the
    case.
    """
    try:
        value = float(getattr(ctx, "budget_s", None) or SUBMIT_BUDGET_S)
    except (TypeError, ValueError):
        value = SUBMIT_BUDGET_S
    return value if value > 0 else SUBMIT_BUDGET_S


def budget_stop_reason(ran: int, total: int) -> str:
    """What the model is told when the wall clock ended a replay early."""
    return (
        "budget reached after "
        + str(int(ran))
        + " action(s); the remaining "
        + str(max(0, int(total) - int(ran)))
        + " actions were not run; continue from the screen shown. One submit "
        "replays for at most "
        + str(int(SUBMIT_BUDGET_S))
        + "s, so send a shorter script."
    )


async def replay(script: object, ctx: Context) -> dict:
    """Execute *script* against ``ctx``. Never raises.

    ``{"error": None, "content": {"status", "trace", "screen", "verdict",
    "reason", "stopped_at"}}``. Top-level ``error`` is reserved for this
    function itself failing -- a device refusal is ``status == "error"`` with a
    trace, because the trace is the only thing that tells a tester how far the
    case got.
    """
    trace: list[dict] = []
    screen = ctx.screen if isinstance(ctx.screen, dict) else None
    try:
        items = list(getattr(script, "actions", None) or [])
        if not items:
            return {
                "error": None,
                "content": _result(
                    STATUS_ERROR, trace, screen, "", "The script had no actions.", -1
                ),
            }
        if screen is None:
            dumped = await _dump(ctx)
            if dumped.get("error"):
                return {
                    "error": None,
                    "content": _result(
                        STATUS_ERROR, trace, None, "", str(dumped["error"]), -1
                    ),
                }
            screen = dumped.get("content")

        deadline = time.monotonic() + _budget_seconds(ctx)
        #: The screen content as it was before the last screen-changing action.
        #: ``None`` until one has run, which is the honest answer ``new_text``
        #: gives when a script asserts a reply before sending anything.
        baseline_texts: set | None = None
        for index, action in enumerate(items):
            # NEVER on index 0. A budget that can stop the first action makes no
            # progress at all, and each stop costs one of three escapes, so a
            # short budget would turn every case into `blocked` without ever
            # touching the device.
            if index and time.monotonic() >= deadline:
                return {
                    "error": None,
                    "content": _result(
                        STATUS_NEEDS_MODEL,
                        trace,
                        screen,
                        "",
                        budget_stop_reason(index, len(items)),
                        index,
                        # NOT an escape. The budget can stop a script the
                        # validator called legal -- the per-wait re-dump costs
                        # wall clock that the wait cap does not count -- and
                        # charging one of three escapes for obeying our own
                        # bound is how a correct case becomes `blocked`.
                        budget_stop=True,
                    ),
                }
            started = time.monotonic()
            entry = _entry(index, action, _screen_id(screen), started)
            op = str(getattr(action, "op", "") or "")
            # The BEFORE set for ``assert new_text``, captured for every op that
            # can change the screen -- ``wait`` included, which is the whole
            # point: the reply a case is waiting for arrives during the wait.
            if op in actions_mod.MUTATING_OPS:
                baseline_texts = _texts(screen)

            # --- terminal ops -------------------------------------------------
            if op == "done":
                verdict = str(getattr(action, "verdict", ""))
                reason = str(getattr(action, "reason", ""))[:400]
                if verdict == "pass" and not _has_verification(trace):
                    verdict = STATUS_UNVERIFIED
                    reason = (
                        "verdict=pass was not accepted: this case's trace has "
                        "no assert_pass, so nothing was verified. Add an "
                        "`assert` for what the case is supposed to show. " + reason
                    ).strip()
                entry["outcome"] = "done"
                entry["detail"] = reason
                entry["after_screen_id"] = _screen_id(screen)
                _append(trace, entry)
                return {
                    "error": None,
                    "content": _result(
                        STATUS_DONE,
                        trace,
                        screen,
                        verdict,
                        reason,
                        index,
                    ),
                }
            if op == "ask_tester":
                field = str(getattr(action, "field", ""))
                supplied = (ctx.tester_inputs or {}).get(field)
                if supplied is None:
                    entry["outcome"] = "needs_tester"
                    entry["detail"] = str(getattr(action, "prompt", ""))[:300]
                    entry["after_screen_id"] = _screen_id(screen)
                    _append(trace, entry)
                    return {
                        "error": None,
                        "content": _result(
                            STATUS_NEEDS_TESTER,
                            trace,
                            screen,
                            "",
                            entry["detail"],
                            index,
                            field=field,
                        ),
                    }
                entry["outcome"] = "supplied"
                entry["detail"] = "the tester supplied " + field
                entry["after_screen_id"] = _screen_id(screen)
                _append(trace, entry)
                continue

            # --- target resolution -------------------------------------------
            target = getattr(action, "target", None)
            element = None
            if target is not None:
                resolved = actions_mod.resolve_target(target, screen)
                element = ((resolved or {}).get("content") or {}).get("element")
                # ``assert`` is exempt for the same reason ``scroll`` is, and it
                # matters more: a missing element IS the answer an
                # ``assert kind="element"`` was asked for. Letting the generic
                # boomerang intercept it meant that assert could never FAIL --
                # the one verdict it exists to produce. It returned
                # ``needs_model`` instead, so a tester who required an element
                # that was absent got a re-plan, three spent escapes and a
                # ``blocked`` case rather than the failure they asked for.
                # ``_evaluate_assert`` decides presence itself.
                if element is None and op not in ("scroll", "assert"):
                    resolution = (resolved or {}).get("content") or {}
                    entry["outcome"] = "missing_element"
                    entry["detail"] = missing_element_detail(resolution)
                    entry["after_screen_id"] = _screen_id(screen)
                    _append(trace, entry)
                    return {
                        "error": None,
                        "content": _result(
                            STATUS_NEEDS_MODEL,
                            trace,
                            screen,
                            "",
                            entry["detail"],
                            index,
                            # Read by ``case_runner``: a stop caused by OUR
                            # OWN selector going stale, before anything touched
                            # the device, is not the tester's case failing to
                            # make progress -- see MAX_FREE_STOPS there.
                            #
                            # Intersected with ``actions.OURS`` rather than
                            # taken from the bare ``stale`` flag. Measured:
                            # ``{"rid": <gone>, "text": <matches>}`` -- no `id`
                            # anywhere -- reported stale and was UNCHARGED,
                            # while OURS' own docstring said a rid or text
                            # naming something absent is charged like any other
                            # boomerang. A conflict is charged too: two
                            # selectors the MODEL chose disagreeing is a stale
                            # plan, not our bookkeeping.
                            selector_stale=any(
                                name in actions_mod.OURS
                                for name in (resolution.get("stale_selectors") or ())
                            ),
                            actuated=_actuated(trace),
                        ),
                    }

            # --- destructive guard -------------------------------------------
            # DENY BY DEFAULT. This was an allow-list of ops believed to tap,
            # and it was wrong twice: 'clear' taps to focus the field, and a
            # TARGETED 'scroll' re-centres its swipe on the resolved element,
            # so "Slide to confirm payment" was actuated while the same control
            # under 'tap' was stopped. The guard exists to stop an IRREVERSIBLE
            # ACTION; which primitive delivers it is incidental, and an
            # allow-list must be remembered every time one is added.
            #
            # So every op is guarded unless it is declared inert. A target-less
            # pan still passes: it resolves no element and there is no
            # destructive text in "scroll down".
            if ctx.guard_destructive and op not in NON_ACTUATING_OPS:
                label = " ".join(
                    [
                        actions_mod.action_text(action),
                        element_label(element, screen),
                    ]
                ).strip()
                hit = destructive_hit(label)
                if hit:
                    entry["outcome"] = "guard_stop"
                    entry["detail"] = GUARD_DETAIL + " Matched: " + hit
                    entry["after_screen_id"] = _screen_id(screen)
                    _append(trace, entry)
                    return {
                        "error": None,
                        "content": _result(
                            STATUS_NEEDS_TESTER,
                            trace,
                            screen,
                            "",
                            entry["detail"],
                            index,
                            guard_term=hit,
                        ),
                    }

            # --- asserts ------------------------------------------------------
            if op == "assert":
                ok, detail = _evaluate_assert(action, screen, trace, baseline_texts)
                entry["outcome"] = "assert_pass" if ok else "assert_fail"
                entry["detail"] = detail
                entry["after_screen_id"] = _screen_id(screen)
                _append(trace, entry)
                if ok:
                    continue
                return {
                    "error": None,
                    "content": _result(
                        STATUS_NEEDS_MODEL, trace, screen, "", detail, index
                    ),
                }

            # --- wait / until_text ---------------------------------------------
            if op == "wait":
                until_text = str(getattr(action, "until_text", "") or "").strip()
                ms = int(getattr(action, "ms", 0) or 0)
                if until_text:
                    found, screen, _timed_out = await _wait_until_text(
                        ctx, screen, until_text, ms
                    )
                    entry["outcome"] = "ok" if found else "wait_timeout"
                    entry["detail"] = (
                        "found " + repr(until_text[:120])
                        if found
                        else "timed out waiting for " + repr(until_text[:120])
                    )
                    if found:
                        # No re-dump: the poll already left a fresh screen.
                        screen, stop = await _settle(
                            ctx,
                            entry,
                            screen,
                            trace,
                            index,
                            redump=False,
                            mark_no_change=False,
                            op=op,
                        )
                        if stop is not None:
                            return {"error": None, "content": stop}
                        entry["after_screen_id"] = _screen_id(screen)
                        _append(trace, entry)
                        continue
                    entry["after_screen_id"] = _screen_id(screen)
                    _append(trace, entry)
                    return {
                        "error": None,
                        "content": _result(
                            STATUS_NEEDS_MODEL,
                            trace,
                            screen,
                            "",
                            entry["detail"],
                            index,
                        ),
                    }
                if ms:
                    await _sleep(ms / 1000.0)
                entry["outcome"] = "ok"
                entry["detail"] = "waited " + str(ms) + "ms"
                screen, stop = await _settle(
                    ctx,
                    entry,
                    screen,
                    trace,
                    index,
                    redump=True,
                    mark_no_change=False,
                    op=op,
                )
                if stop is not None:
                    return {"error": None, "content": stop}
                entry["after_screen_id"] = _screen_id(screen)
                _append(trace, entry)
                continue

            # --- device ops ---------------------------------------------------
            outcome = await _perform(op, action, element, ctx)
            if outcome.get("error"):
                if outcome.get("mask_text") and isinstance(entry.get("action"), dict):
                    for key in ("text", "value"):
                        if key in entry["action"]:
                            entry["action"][key] = actions_mod.SECRET_MASK
                recoverable = bool(outcome.get("needs_model"))
                entry["outcome"] = "refused" if recoverable else "device_error"
                entry["detail"] = str(outcome["error"])[:400]
                entry["after_screen_id"] = _screen_id(screen)
                _append(trace, entry)
                return {
                    "error": None,
                    "content": _result(
                        PASSWORD_REFUSAL_STATUS if recoverable else STATUS_ERROR,
                        trace,
                        screen,
                        "",
                        entry["detail"],
                        index,
                    ),
                }
            entry["outcome"] = "ok"
            entry["detail"] = str((outcome.get("content") or {}).get("detail") or "")

            if op in actions_mod.MUTATING_OPS:
                screen, stop = await _settle(
                    ctx,
                    entry,
                    screen,
                    trace,
                    index,
                    redump=True,
                    mark_no_change=True,
                    op=op,
                )
                if stop is not None:
                    return {"error": None, "content": stop}
            entry["after_screen_id"] = _screen_id(screen)
            _append(trace, entry)

        # A script that never calls done() lands here. It used to hand back an
        # empty verdict, which `case_runner` reads as PASS -- so omitting one
        # word bypassed the whole verification rule the done() branch enforces.
        # The same rule applies on both exits or it is not a rule.
        ended_verified = _has_verification(trace)
        # A VERIFIED run must say what it verified. The reason used to open
        # "The script finished without a done() action" on BOTH exits, so a
        # tester reading a pass was told about an omission in the script rather
        # than about the app. The omission is still disclosed -- second
        # sentence -- because it is why there is no model-written reason here.
        reason = (
            (
                "Verified: "
                + (_verification_summary(trace) or "the asserts in this script passed")
                + ". The script ended without a done() action, so this verdict "
                "rests on the asserts above."
            )
            if ended_verified
            else (
                "The script finished without a done() action. Nothing in it "
                "asserted anything, so there is no evidence this case passed "
                "-- a screen that moved is activity, not verification."
            )
        )
        return {
            "error": None,
            "content": _result(
                STATUS_DONE,
                trace,
                screen,
                "" if ended_verified else STATUS_UNVERIFIED,
                reason,
                len(items) - 1,
            ),
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.executor.replay failed")
        return {"error": str(exc), "content": None}


def _append(trace: list[dict], entry: dict) -> None:
    """Stamp the action's duration and keep the entry. The private ``_started``
    mark never reaches the trace: it is popped here, on every path."""
    started = entry.pop("_started", None)
    if started is not None:
        entry["ms"] = int(max(0.0, time.monotonic() - float(started)) * 1000)
    if len(trace) < MAX_TRACE:
        trace.append(entry)


#: Ops that really touch the device. ``actions.MUTATING_OPS`` minus ``wait``:
#: a wait forces a re-dump (which is why it is a mutating op there) but it
#: actuates nothing, and counting it as progress charged an escape for a stop
#: that had moved nothing. Derived from that frozenset rather than restated, so
#: an op added there is covered here by construction.
ACTUATING_OPS: frozenset[str] = frozenset(actions_mod.MUTATING_OPS) - {"wait"}

#: Why a target did not resolve, in the words the model reads on its next turn.
#: Constants so a test asserts this module's own text instead of a copy of it,
#: and so the three cases stay distinguishable in a trace a tester reads.
MISS_PLAIN = (
    "No element on this screen matched that target, so the replay stopped here "
    "rather than tapping something else."
)
#: The LONE-selector case, and the one a model meets most often: a target
#: carrying only an `id`, after a `type` moved every element on the screen.
#: It keeps the REMEDY, which is the whole value of the message -- a generic
#: "a selector did not resolve" leaves the model to re-plan by `id` again and
#: burn both free stops and then real escapes.
MISS_STALE_ID = (
    "The `id` in that target is not on this screen. An id describes an element "
    "as it was on the screen you planned from, and this screen has changed, so "
    "nothing was tapped. Re-plan from the screen below, and prefer `rid` or the "
    "exact on-screen text -- those survive a re-layout, an id does not."
)

#: EVERY selector missed, and more than one was supplied. Its remedy cannot be
#: "prefer `rid` or the text" -- those are what just failed. Measured: three of
#: the four shapes that used to reach MISS_STALE_ID supplied a rid and/or a text
#: that also matched nothing, so the message recommended the selectors the model
#: had just tried, and the model re-planned the same way and spent its free
#: stops. Nothing it said was false, which is why only a reachability predicate
#: tight enough to separate the shapes could catch it.
MISS_STALE_ALL = (
    "None of that target's selectors matches anything on this screen, so the "
    "plan was built on a screen that has since changed and nothing was tapped. "
    "Do not retry the same selectors. Re-plan from the screen below: take the "
    "`rid` or the exact text of the control you want from THIS element list."
)

#: The CROSS-SELECTOR case: two or more selectors were supplied and at least one
#: of them resolved. Only reachable then, which
#: ``test_every_miss_message_is_reachable_and_accurate`` asserts against the
#: 63-target enumeration -- because this wording shipped on the lone-id path,
#: telling a model that two selectors disagreed when it had sent one.
MISS_STALE_SELECTOR = (
    "One selector in that target matches nothing on this screen while another "
    "one does, so the plan was built on a screen that has since changed and "
    "nothing was tapped. Re-plan from the screen below. If the stale one was "
    "the `id`, prefer `rid` or the exact on-screen text -- those survive a "
    "re-layout, an id does not."
)
MISS_CONFLICT = (
    "That target's selectors point at DIFFERENT elements on this screen, which "
    "means the plan was made against an older one. Nothing was tapped and none "
    "of them was chosen. Re-plan from the screen below."
)


def _actuated(trace: object) -> bool:
    """Whether anything in THIS submit has already touched the device.

    The distinction the escape budget needs. A stop AFTER a tap or a type is a
    case that made progress and was handed back -- exactly what an escape is
    for. A stop BEFORE any of them, caused by one of our own selectors having
    gone stale, is bookkeeping, and ``case_runner`` declines to charge for it.

    Reads :data:`ACTUATING_OPS` rather than ``actions.MUTATING_OPS``, and the
    difference is one op that matters: ``wait`` is in MUTATING_OPS because a
    wait exists precisely to let the screen change, but it touches nothing. A
    script of ``[wait, tap(stale id)]`` was therefore charged an escape for a
    stop that actuated NOTHING -- a third of ``MAX_ESCAPES`` spent on the exact
    case ``MAX_FREE_STOPS`` was added to protect, while this docstring claimed
    the opposite.
    """
    for entry in list(trace or []):
        if not isinstance(entry, dict):
            continue
        if str(entry.get("outcome") or "") not in ("ok", "no_change"):
            continue
        action = entry.get("action")
        action = action if isinstance(action, dict) else {}
        if str(action.get("op") or "") in ACTUATING_OPS:
            return True
    return False


def _result(
    status: str,
    trace: list[dict],
    screen: object,
    verdict: str,
    reason: str,
    stopped_at: int,
    **extra: object,
) -> dict:
    payload = {
        "status": str(status),
        "trace": list(trace),
        "screen": screen if isinstance(screen, dict) else None,
        "verdict": str(verdict or ""),
        "reason": str(reason or ""),
        "stopped_at": int(stopped_at),
    }
    payload.update(extra)
    return payload


def _evaluate_assert(
    action: object,
    screen: object,
    trace: list[dict],
    baseline: object = None,
) -> tuple[bool, str]:
    """One assert against the current screen.

    *baseline* is the content set of the screen as it was before the last
    screen-changing action, and only ``new_text`` reads it.

    ``screen_changed`` is deliberately kept and deliberately WEAK: it compares
    ids, so any navigation satisfies it. It is not evidence that an app
    answered, and ``new_text`` exists because a live run read it as if it were.
    """
    kind = str(getattr(action, "kind", "") or "")
    text = str(getattr(action, "text", "") or "")
    if kind == "text_present":
        found = _screen_has(screen, text)
        return found, ("found " if found else "NOT found on this screen: ") + repr(
            text[:120]
        )
    if kind == "text_absent":
        found = _screen_has(screen, text)
        return (not found), (
            "still on this screen: " if found else "absent, as expected: "
        ) + repr(text[:120])
    if kind == "element":
        resolved = actions_mod.resolve_target(getattr(action, "target", None), screen)
        body = (resolved or {}).get("content") or {}
        element = body.get("element")
        if element is None and (body.get("stale") or body.get("conflict")):
            # "The element is absent" and "the target could not be read on this
            # screen" are different answers, and an assert must not quietly
            # turn the second into the first -- that would report the app as
            # wrong when the PLAN was stale.
            return False, missing_element_detail(body)
        return bool(element), (
            "the element is on this screen"
            if element
            else "the element is not on this screen"
        )
    if kind == "new_text":
        if baseline is None:
            return False, (
                "no earlier screen to compare against: nothing in this script "
                "has changed the screen yet, so there is no 'before' to diff"
            )
        contains = " ".join(str(getattr(action, "contains", "") or "").split())
        # TWO things are subtracted before anything counts as a reply, and both
        # came from a review that ran the code rather than read it.
        #
        # 1. What this script TYPED. A chat composer echoes your question into
        #    the thread, so `[type, tap send, assert new_text]` passed on an app
        #    that never answered -- on the most ordinary screen there is.
        # 2. Furniture that only CHANGED. A clock going 10:42 -> 10:43 is a new
        #    string and is not new content. Recognised without a threshold: an
        #    added string whose digits normalise onto a string that was there
        #    before is a mutation of what was already on screen.
        added = sorted(_texts(screen) - set(baseline))
        typed = _typed_by_script(trace)
        added = [item for item in added if item not in typed]
        before_shapes = {
            _digit_shape(item) for item in set(baseline) if _is_furniture(item)
        }
        # ONE condition, not two. The bound belongs on the BASELINE side -- only
        # a string that was already furniture can be furniture that ticked -- and
        # a second copy of it on the added side decided nothing, which mutation
        # proved by deleting it and changing no result. A guard nothing can kill
        # is not a guard.
        added = [item for item in added if _digit_shape(item) not in before_shapes]
        if contains:
            added = [item for item in added if contains.lower() in item.lower()]
        if added:
            return True, (
                "a new reply appeared: "
                + repr(added[0][:160])
                + (
                    (" (and " + str(len(added) - 1) + " more)")
                    if len(added) > 1
                    else ""
                )
            )
        return False, (
            "no new text appeared on this screen since the last action"
            + ((" containing " + repr(contains[:60])) if contains else "")
        )

    # screen_changed
    seen = [
        entry.get("before_screen_id")
        for entry in trace
        if entry.get("before_screen_id")
    ]
    current = _screen_id(screen)
    if not seen:
        return False, "no earlier screen to compare against"
    changed = current != seen[0]
    return changed, (
        "the screen changed since the first action"
        if changed
        else "the screen did not change since the first action"
    )


async def _perform(op: str, action: object, element: object, ctx: Context) -> dict:
    """One device op. Every argv is built by ``adb``, which validates again."""
    serial = ctx.serial
    if op == "back":
        return await adb.keyevent(serial, "KEYCODE_BACK")
    if op == "home":
        return await adb.keyevent(serial, "KEYCODE_HOME")
    if op == "launch":
        if not ctx.package:
            return {"error": "No app package is set for this run.", "content": None}
        return await adb.launch(serial, ctx.package)
    if op == "open_url":
        return await adb.open_url(serial, str(getattr(action, "url", "")))
    if op == "wait":
        ms = int(getattr(action, "ms", 0) or 0)
        if ms:
            await _sleep(ms / 1000.0)
        return {"error": None, "content": {"detail": "waited " + str(ms) + "ms"}}
    if op == "scroll":
        points = _SWIPE.get(str(getattr(action, "dir", "") or ""))
        if not points:
            return {"error": "Unknown scroll direction.", "content": None}
        if isinstance(element, dict):
            center = _center(element)
            if center:
                dx = center[0] - 540
                points = (
                    points[0] + dx,
                    points[1],
                    points[2] + dx,
                    points[3],
                )
        return await adb.swipe(serial, *points)
    if op == "tap":
        center = _center(element) if isinstance(element, dict) else None
        if not center:
            return {"error": "That element has no usable bounds.", "content": None}
        return await adb.tap(serial, center[0], center[1])
    if op in ("type", "clear"):
        center = _center(element) if isinstance(element, dict) else None
        if not center:
            return {"error": "That field has no usable bounds.", "content": None}
        focused = await adb.tap(serial, center[0], center[1])
        if focused.get("error"):
            return focused
        if op == "clear":
            return await ime.clear(serial)
        # A PASSWORD INPUT TAKES ONLY A TESTER-SUPPLIED VALUE. Refused by name
        # rather than typed and masked afterwards, and decided on the ELEMENT
        # rather than on the action's chosen names: a field named in an alphabet
        # this server cannot read is exactly the case a name-matching rule
        # cannot judge, and it is also the case where the dump still says
        # `password="true"`.
        secret = bool(getattr(action, "secret", False))
        if (
            not secret
            and isinstance(element, dict)
            and (element.get("secure") or element.get("role") == "password")
        ):
            return {
                "error": PASSWORD_REFUSAL,
                "content": None,
                # NEEDS_MODEL rather than the device_error every other `_perform`
                # failure becomes: this one names a recovery, and an error ends
                # the case before the model can take it.
                "needs_model": True,
                # REFUSING TO TYPE IT IS NOT ENOUGH. The trace entry was built
                # before the element was known, so it still carries the literal
                # -- and the trace is checkpointed, rendered and audited. This
                # asks the caller to mask it, which is the only place that can:
                # `redact_action` sees the action alone and this value is a
                # credential because of the ELEMENT it was aimed at.
                "mask_text": True,
            }
        if secret:
            field = str(getattr(action, "field", "") or "")
            value = (ctx.tester_inputs or {}).get(field)
            if value is None:
                return {
                    "error": (
                        "No tester-supplied value is held for the field "
                        + repr(field[:60])
                        + ", so nothing was typed. Ask for it first with "
                        "ask_tester."
                    ),
                    "content": None,
                }
            return await ime.type_text(serial, str(value), secret=True)
        return await ime.type_text(
            serial, str(getattr(action, "text", "") or ""), secret=False
        )
    return {"error": "Unsupported action " + repr(op), "content": None}


async def _sleep(seconds: float) -> None:
    """Named so a test can patch it; a real sleep would be the slowest thing here."""
    import asyncio

    await asyncio.sleep(seconds)
