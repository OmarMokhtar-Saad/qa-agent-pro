"""The exploration CHARTER: the terms one explore run is conducted under.

``qa_mobile_test`` hands the tester's model an INTAKE packet before an explore
run starts. The SERVER owns the question set and the typed schema in this
module; the tester's model owns only the wording it puts them in. That division
is the whole point: a model that improvises the questions gives every run a
different charter shape, so nothing is resumable and two runs of "the same"
exploration are not comparable.

Four properties this module holds. Each is pinned in
``tests/mobile/test_mobile_charter.py``, and each pin has a mutant listed in
that file's module docstring -- per the authoring rule, a line here may claim a
property is graded only once its mutant has been RUN.

* **At most :data:`MAX_QUESTIONS` questions are ever asked.** More and the
  tester abandons the flow before the run starts. :data:`QUESTIONS` is a tuple,
  and :func:`questions_for` slices to the cap, so the packet builder cannot
  exceed it however it is called.
* **An unanswered field takes its default AND the report NAMES the default it
  used.** :func:`defaults_used` is the one producer of that list and
  :func:`describe_terms` the one producer of the sentence the report renders.
  "Unanswered" is judged against the RAW input, never against the normalised
  charter: normalising fills every field, so a charter compared against itself
  reports nothing was set -- and a tester who answered ``depth: both`` would be
  told they did not answer. The verdict is therefore computed ONCE, in
  :func:`normalize`, and carried in :data:`DEFAULTS_KEY` so every later reader
  -- the report, a resume in another chat -- reads the same verdict rather than
  re-deriving it from data that can no longer support it.
* **The charter is persisted in the run manifest.** It is stored as a key of
  the explore STATE, which ``run_store`` writes as ``manifest["explore"]``, so
  a resume from another chat explores under the same terms -- which is why it
  is typed here rather than left as prose in a packet.
* **``destructive`` decides whether a guard hit PAUSES the run or REFUSES it,
  and NOTHING else.** :func:`guard_policy` is that whole decision. It does not
  widen the destructive lexicon, ``tools/mobile/executor.py`` does not import
  this module, and the guard -- including the per-element fidelity sentinel
  added by step 3 -- is UNCHANGED. A future reader will assume otherwise, so it
  is said here: there is no value of ``destructive`` that lets an action
  through the guard. ``allowed`` means the tester has pre-authorised what the
  guard stops on, so the pause is a one-line confirm; it is not a bypass.
  **The REFUSE half IS WIRED, since step 5.** ``session._submit_explore``
  reads :func:`guard_policy` for the run's own charter and sets
  ``executor.Context.guard_refuses``; at the one guard-hit site a refusing
  run gets the refusal detail and a TERMINAL status instead of the tester
  pause, and the stop is recorded on the explore state. What is STILL
  unwired keeps its own disclosure -- ``coverage_plateau``, which nothing
  detects. This is the module's FRONT PAGE, and it is retired in the same
  commit that wired the thing it described: a disclosure that outlives its
  gap is the same defect as one that precedes the fix.

**There is no ``credentials`` field, deliberately, and it is not an oversight
a later reader should quietly correct.** The schema carried one for exactly one
revision. It had NO consumer: nothing read it but the sentence that printed it,
so its entire observable effect was writing secret-adjacent free text into a
report that lands on disk. A field with no consumer and a live leak route is
pure risk, so it was DELETED. A future step that genuinely needs to name a
credential FIELD must build it with an ALLOWLIST of known field names -- never
a filter that tries to tell a one-word NAME from a one-word VALUE, which is not
something a filter can do by construction: ``hunter2``, ``1234``, ``000000``
and ``otp#1234`` are all single whitespace-free tokens carrying no separator.
Whatever it builds must not re-open what ``tools/mobile/ime.py`` guarantees:
the lane types credential VALUES through the IME on stdin with an EMPTY argv
(``type_text`` -> ``adb.shell(serial, [], stdin_data=...)``) precisely so a
value never reaches argv, a packet, a trace or the run store, and
``agents/mobile_run.build_tester_request`` carries a field NAME and a prompt
and has no way to carry a value.

No model call, no device, no file and no network: this module is data and
coercion. Every coercion catches ``OverflowError`` beside ``TypeError`` and
``ValueError``, because ``json.loads`` admits ``Infinity`` and
``int(float("inf"))`` raises the third one.

Not wired, stated rather than implied: ``stop_on="coverage_plateau"`` is
accepted, persisted and REPORTED, and behaves as ``budget``. Nothing detects a
plateau yet, so nothing grades a plateau stop -- follow-up. The report says so
in words rather than letting a tester believe a stop condition applied.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

#: The hard cap on questions ONE intake packet may ask. Five is the
#: requirement, not a tuning knob: past it testers abandon the flow before the
#: run starts, and a charter nobody finished answering is no charter at all.
MAX_QUESTIONS = 5

#: Include/exclude lines one charter may carry, and characters of one line.
MAX_SCOPE_ITEMS = 12
MAX_SCOPE_ITEM_CHARS = 200

#: Bytes of the host-submitted charter string this module will parse at all.
MAX_CHARTER_BYTES = 8000

#: The largest steps/minutes figure a charter may carry, and the bound
#: :func:`_int` applies to EVERY number it coerces. A byte cap on the charter
#: STRING does not bound the numbers INSIDE it: ``{"budget": {"steps": 1e300}}``
#: is 30 bytes and ``int(float(1e300))`` is a 301-DIGIT integer, because only
#: ``>= 1e309`` reaches the OverflowError path. Two of those put ~620
#: characters into one clause of :func:`describe_terms`, which the report
#: clips at 800 -- so an unbounded number here is a route to pushing a
#: DISCLOSURE off the page. The value is far above any real ask (the lane's
#: own ceilings clamp a charter downward long before this binds); its job is
#: to bound the RENDERED WIDTH, not to express a policy about run length.
MAX_BUDGET_UNITS = 100000
#: The most OFF-CHARTER screen visits one run records. The bound is the
#: RECORD in the MANIFEST: the list is written on every turn, persisted, and
#: re-read on every resume, so it needs a fence whatever reads it. **Nothing
#: renders it yet.** The writer is ``explore_runner.next_turn``; the readers
#: today are a resume and one pin -- no report page and no packet shows it to
#: a tester, and nothing grades a rendering because nothing implements one --
#: follow-up. The lane's own MAX_TURNS bounds the visits long before this
#: binds.
MAX_OFF_CHARTER_RECORDS = 50

#: The most characters of the screen strings the scope predicate reads.
#: ``perception.MAX_ELEMENTS`` bounds the element count, but each element
#: carries several strings, so the joined haystack is bounded HERE rather than
#: left to the product of two other caps. It bounds WORK per turn, not policy.
MAX_SCOPE_MATCH_CHARS = 4000

#: WHICH strings a scope line may match -- an ALLOWLIST, by name, and the
#: whole of the haystack. The rule in one line: **the scope predicate reads
#: only what the TESTER CAN SEE ON THE SCREEN.**
#:
#: This was key-AGNOSTIC for one revision, and only EXECUTION showed how
#: wrong that was. Taking every string a screen record carries swept in
#: ``screen_id``, ``hash``, ``package``, ``dialog_package`` and ``activity``,
#: and per element ``rid``, ``cls`` and ``package``. MEASURED on the applied
#: tree: excluding "Settings" put a CHECKOUT screen out of scope because the
#: activity was ``com.example.app/.SettingsActivity``; excluding "cab"
#: matched the screen_id ``cab99f10dead``; "fee" matched the hash
#: ``feed00112233abcd``; "ad" matched ``deadbeefcafe0001``. The activity case
#: is the bad one, because the activity is usually the SAME across an app's
#: screens: every screen then reads out_of_scope, the packet tells the model
#: to send ``back`` every turn, and the run spends its whole budget going
#: backwards while reporting normally.
#:
#: The agnostic form was sold as a safety property -- "a provider that
#: renames a field cannot silently empty the haystack". It did buy that, and
#: the honest statement of the trade is that it bought it BY MATCHING
#: IDENTIFIERS THE TESTER NEVER WROTE. ``out_of_scope`` is meant to be a
#: POSITIVE identification, and a two-character line hitting a hex digest
#: identifies nothing. The allowlist takes the opposite risk KNOWINGLY: a
#: renamed field CAN empty the haystack, and the failure is then ``in_scope``
#: or ``not_confirmed`` -- the direction that explores a screen it might have
#: avoided, rather than the direction that abandons the whole app. A provider
#: that adds a tester-visible screen title adds its key HERE.
#:
#: ``activity`` is EXCLUDED deliberately: a tester writes flow names, not
#: Android class names, and it is precisely the field that turns one bad
#: match into an app-wide one. ``screen_id``, ``hash``, ``package`` and
#: ``dialog_package`` are NEVER admissible by any future edit -- they are
#: identifiers by construction.
#:
#: Empty TODAY: ``perception`` emits no screen-level string a tester reads.
#: It is named rather than inlined so the providers step has one obvious
#: place to extend, and so the exclusions above have something to be an
#: exclusion FROM.
SCOPE_SCREEN_FIELDS = ()

#: The per-element strings the same rule admits: the visible label and the
#: accessibility description. NOT ``rid`` and NOT ``cls`` -- the same defect
#: class with the same app-wide blast radius: excluding "view" would match
#: ``TextView`` on every element of every screen, and ``id/settings_row`` is
#: a developer's name, not the tester's. NOT ``package``, ever.
SCOPE_ELEMENT_FIELDS = ("text", "desc")

#: What each ``depth`` asks the tester's model to ATTEMPT, one sentence each.
#: ONE producer of the directive (:func:`depth_directive`), and deliberately
#: NOT a tactics catalogue: it enumerates no per-element tactic and records no
#: tactic coverage, because a catalogue is a later step with its own design
#: questions. What it does is the thing the field was typed for -- the packet
#: asks for different WORK under different depths.
DEPTH_DIRECTIVES = {
    "happy": (
        "Attempt only the intended, successful path through this screen. Do "
        "not try to make anything fail this run."
    ),
    "negative": (
        "Attempt the ways this screen can be made to fail or refuse -- empty, "
        "over-long and wrong-shaped input, and actions taken out of order. Do "
        "not spend turns re-walking the intended path."
    ),
    "both": (
        "Walk the intended path through this screen first, then, on the SAME "
        "screen, probe one way it can be made to fail or refuse."
    ),
}

#: The three scope verdicts. THREE, not two, and the third IS the design:
#: "no include line matched" is NOT "out of bounds". A screen whose wording
#: differs from the tester's would otherwise stop a run on its second turn, so
#: the undeterminable answer is DISCLOSED to the model and never acted on.
SCOPE_IN = "in_scope"
SCOPE_OUT = "out_of_scope"
SCOPE_UNCONFIRMED = "not_confirmed"

#: Said to the model when a screen matches a line the charter put OUT of
#: scope. A NAMED record plus a next move, not a stop: nothing in this server
#: ends a run on it -- nothing grades a scope-driven stop, because nothing
#: implements one, follow-up.
OFF_CHARTER_NOTE = (
    "This screen matches a flow the charter put OUT of scope. Do not explore "
    "it: send `back`, say in `finding` that the run reached an out-of-scope "
    "screen and name it, then carry on inside the scope."
)

#: Said when the charter names INCLUDE lines and none matched. The honest form
#: of "I cannot tell": the model is told what was asked for and asked to
#: judge, rather than being told a screen is out of bounds on the strength of
#: a substring that did not match.
UNCONFIRMED_NOTE = (
    "Nothing on this screen matched the flows the charter named, so the "
    "server cannot confirm it is in scope. Judge it yourself against the "
    "scope lines above, and say in `finding` if you believe it is outside."
)

#: Where :func:`normalize` records WHICH fields the tester left unanswered.
#: It is metadata about the answering, not a term of the run, so it is not a
#: key of :func:`defaults`; it is carried in the normalised charter because the
#: verdict can only be computed from the RAW input and every later reader has
#: only the normalised one.
DEFAULTS_KEY = "defaults_used"

#: Where :func:`normalize` records the fields the tester DID answer whose
#: answer a coercer REPLACED. Same channel, same strip and the same anti-spoof
#: argument as :data:`DEFAULTS_KEY`: it is this module's own output, so
#: :func:`normalize` drops any copy a host sends before deriving its own.
REJECTED_KEY = "rejected_fields"

#: The fields whose value a coercer can SILENTLY SUBSTITUTE -- the closed
#: enums and the two budget axes. Everything else is free text that is
#: bounded, never replaced.
REJECTABLE = ("depth", "destructive", "stop_on", "budget.steps", "budget.minutes")

DEPTHS = ("happy", "negative", "both")
DESTRUCTIVE = ("none", "reversible", "allowed")
STOP_ON = ("first_finding", "budget", "coverage_plateau")

#: What a guard hit DOES under this charter. Two values, and only two, and
#: BOTH are implemented: ``PAUSE`` stops for the tester and ``REFUSE`` ends
#: the attempt by name -- see :func:`guard_policy` for which value maps where
#: and for the one site that reads it.
PAUSE = "pause"
REFUSE = "refuse"

#: The stop conditions step 4 actually WIRES. ``coverage_plateau`` is absent on
#: purpose and :func:`describe_terms` discloses it.
WIRED_STOPS = ("first_finding", "budget")

#: The server's question set. FIVE, no more, and the field it does NOT ask --
#: ``stop_on`` -- is the one with an answer that is safe and complete without
#: asking (stop when the budget runs out). It is taken from a charter that
#: volunteers it and is otherwise defaulted and NAMED in the report.
QUESTIONS = (
    {
        "field": "goal",
        "ask": "What should the app be made to do? One sentence.",
        "options": (),
    },
    {
        "field": "depth",
        "ask": "Should this run try the happy path, try to break things, or both?",
        "options": DEPTHS,
    },
    {
        "field": "scope",
        "ask": (
            "Any screens or flows to stay inside, and any to stay out of? "
            "Answer as include and exclude lists; empty means the whole app."
        ),
        "options": (),
    },
    {
        "field": "destructive",
        "ask": (
            "May the run take actions that change data -- none, reversible "
            "ones only, or all of them once the tester has said yes? With "
            "`none`, which is also what an unanswered charter gets, a stopped "
            "action ENDS the run rather than pausing for you; ask for "
            "`reversible` if you would rather be asked and carry on."
        ),
        "options": DESTRUCTIVE,
    },
    {
        "field": "budget",
        "ask": "How many steps and how many minutes may this run spend?",
        "options": (),
    },
)

#: The fields a question exists for. Derived, never listed twice.
ASKABLE = tuple(question["field"] for question in QUESTIONS)


def defaults() -> dict:
    """A FRESH default charter. A function, so no caller can mutate the table."""
    return {
        "goal": "",
        "depth": "both",
        "scope": {"include": [], "exclude": []},
        "destructive": "none",
        "budget": {"steps": 0, "minutes": 0},
        "stop_on": "budget",
    }


def _one_line(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _int(value: object, fallback: int = 0) -> int:
    """A BOUNDED integer. Never raises, and never returns an unbounded one.

    The clamp is not tidiness. ``json.loads`` accepts ``1e300`` happily and
    ``int(float(1e300))`` is a 301-digit integer -- only ``>= 1e309`` reaches
    the ``OverflowError`` path -- so a charter well inside
    :data:`MAX_CHARTER_BYTES` could put two such numbers into the budget
    clause of :func:`describe_terms` and push the CHARTER clause that follows
    it past the report's 800-character clip. The clip PROPERTY survives; the
    sentence it used to name does not -- "NOT yet steering" was retired in
    the same commit that wired depth, scope and destructive -- so this names
    the clause by POSITION instead, exactly the way op 8 re-points the clip
    pin. A disclosure a caller can
    push off the page is a disclosure that was not made, so the bound is on
    the VALUE, at the one producer of it. Graded by mutant M12.
    """
    try:
        number = int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return fallback
    return max(-MAX_BUDGET_UNITS, min(MAX_BUDGET_UNITS, number))


def _choice(value: object, allowed: tuple, fallback: str) -> str:
    text = _one_line(value, 40).lower()
    return text if text in allowed else fallback


def _items(raw: object) -> list:
    """A scope list: bounded lines, blanks dropped, never raises.

    A NON-LIST is REJECTED outright rather than coerced. ``list("home")`` is
    ``['h', 'o', 'm', 'e']``, so coercing a JSON string here would turn one
    scope line into four one-character scope entries -- each of which then
    reaches the model on every turn and the tester in the report. A caller that
    sends a string sent the wrong shape, and the honest answer to the wrong
    shape is an empty scope, which the report then NAMES as a default used.
    """
    if not isinstance(raw, (list, tuple)):
        return []
    out: list = []
    for item in list(raw)[:MAX_SCOPE_ITEMS]:
        text = _one_line(item, MAX_SCOPE_ITEM_CHARS)
        if text:
            out.append(text)
    return out


def _unset(body: object, field: str) -> bool:
    """Did the RAW input leave *field* for the server to decide?

    Absent, ``None``, or empty counts as unanswered; anything else -- including
    an answer that happens to equal the default -- counts as ANSWERED. That
    distinction is the whole reason this is judged against the raw input: a
    normalised charter has every field filled, so comparing one against the
    defaults cannot tell "unset" from "set to the default value", and a tester
    who answered ``depth: both`` would be told they did not answer.
    """
    if not isinstance(body, dict) or field not in body:
        return True
    value = body.get(field)
    if value is None:
        return True
    if isinstance(value, (str, list, tuple, dict, set)) and not value:
        return True
    return False


def _sent(value: object) -> str:
    """The tester's own answer, bounded, for a refusal message.

    Not :func:`_one_line`: that renders ``0`` as ``""`` (``str(value or "")``),
    and ``0`` is a value a tester can legitimately send for a budget axis --
    rendering it as empty would report an answered field as substituted.
    """
    return " ".join(str(value).split())[:40]


def _rejected(body: object) -> list:
    """The fields whose ANSWER was REPLACED, what was sent, and what was used.

    **This exists because a coercer is a REFUSAL.** :func:`_choice` replaces an
    out-of-enum answer with the field's default, and :func:`_unset` counts the
    field as ANSWERED -- the raw value is a non-empty string -- so it appears
    in NO defaults clause and in no clamp clause, and :func:`describe_terms`
    then renders the SUBSTITUTED value in the present indicative. Measured on
    the shipped code: ``stop_on: "first-finding"`` became ``budget`` and the
    report said "stopping on budget"; ``depth: "negatives"`` became ``both``;
    ``destructive: "yes"`` became ``none``. ``stop_on`` is WIRED, so that is a
    change of BEHAVIOUR and not only of wording -- a tester who asked to stop
    on the first finding got a run that does not.

    That violates the invariant this change itself adopted: **every site that
    refuses or reduces what the model asked for must refuse BY NAME in the
    same reply.** The asymmetry was the tell -- :func:`parse` refuses oversize
    and unparseable input by name, the budget clamp names the axis that
    clamped, the extension refuses by name, and the enum fallback alone stayed
    silent. The verdict is derived HERE, from the RAW body, once, because the
    normalised charter carries only the substituted value and cannot support
    the derivation afterwards.

    One mutant per coerced field (M13a ``depth``, M13b ``destructive``,
    M13c ``stop_on``, M13d ``budget``): a single-field fixture would grade the
    fixture and not the rule.
    """
    src = body if isinstance(body, dict) else {}
    blank = defaults()
    out: list = []
    for field, allowed in (
        ("depth", DEPTHS),
        ("destructive", DESTRUCTIVE),
        ("stop_on", STOP_ON),
    ):
        if _unset(src, field):
            continue
        used = _choice(src.get(field), allowed, blank[field])
        if used != _sent(src.get(field)).lower():
            out.append(
                {
                    "field": field,
                    "sent": _sent(src.get(field)),
                    "used": used,
                    "choices": list(allowed),
                }
            )
    budget = src.get("budget")
    budget = budget if isinstance(budget, dict) else {}
    for axis in ("steps", "minutes"):
        if _unset(budget, axis):
            continue
        used_number = max(0, _int(budget.get(axis), 0))
        if _sent(budget.get(axis)) != str(used_number):
            out.append(
                {
                    "field": "budget." + axis,
                    "sent": _sent(budget.get(axis)),
                    "used": str(used_number),
                    "choices": ["a whole number from 0 to " + str(MAX_BUDGET_UNITS)],
                }
            )
    return out


def rejected_fields(charter: object) -> list:
    """The refusals for this charter. ONE producer, :func:`defaults_used`'s shape.

    Given a RAW charter this derives them; given a NORMALISED one it returns
    what :func:`normalize` recorded, because the normalised charter carries
    only the substituted values. Host spoofing is closed exactly as it is for
    the defaults verdict: :func:`normalize` strips every non-schema key --
    :data:`REJECTED_KEY` included -- before it derives anything, and every
    field, value and choice returned here is re-bounded on the way out so a
    stored record cannot grow the sentence.
    """
    body = charter if isinstance(charter, dict) else {}
    recorded = body.get(REJECTED_KEY)
    if not isinstance(recorded, (list, tuple)):
        return _rejected(body)
    out: list = []
    for item in recorded:
        if not isinstance(item, dict) or str(item.get("field")) not in REJECTABLE:
            continue
        out.append(
            {
                "field": str(item.get("field")),
                "sent": _sent(item.get("sent")),
                "used": _sent(item.get("used")),
                "choices": [
                    _sent(choice) for choice in list(item.get("choices") or [])[:8]
                ],
            }
        )
    return out


def _schema_only(raw: object) -> dict:
    """The SCHEMA keys of *raw* and NOTHING else. The ONE producer of the strip.

    Named rather than inlined so that it can be FOUND -- by a reader, and by a
    mutation harness. Mutant M2e is ``body = _schema_only(raw_body)`` ->
    ``body = raw_body``; the harness that tried to grade the inline dict
    comprehension this replaced could not locate it and reported neither a kill
    nor a gap. A strip nobody can anchor on is a strip nobody graded. M2e is
    RUN and KILLED. Anchor it on a line number or a code-only pattern: this
    docstring quotes the literal, and a harness that matches the PROSE mutates
    prose and reports a survival that is not evidence about the code.
    """
    body = raw if isinstance(raw, dict) else {}
    return {key: value for key, value in body.items() if key in defaults()}


def normalize(raw: object) -> dict:
    """Any object -> the typed charter. TOTAL: it never raises and never fails.

    An unrecognised value for a closed field becomes that field's DEFAULT
    rather than an error, because a charter is the terms of a run and a run
    that refuses to start over a typo has helped nobody. What the tester did
    not set is recorded HERE, under :data:`DEFAULTS_KEY`, while the raw input
    is still in hand -- it cannot be recovered afterwards -- and that is what
    :func:`defaults_used` returns and the report names.

    **This function is NOT IDEMPOTENT on its own output, deliberately.**
    Re-normalising a charter this function produced strips :data:`DEFAULTS_KEY`
    along with every other non-schema key, and re-derives the verdict against a
    body in which every field is now filled -- so it yields an EMPTY list.
    Making it idempotent would require TRUSTING a :data:`DEFAULTS_KEY` found in
    the input, and the strip below cannot tell a verdict this module wrote from
    one a host sent; that is exactly the spoof route the strip exists to close.
    So the rule is stated rather than hidden: **every reader of the verdict
    must read it off the charter it was GIVEN, never off a fresh normalisation
    of it.** :func:`describe_terms` is the one reader that could get this
    wrong; it does it right, and mutant M11 (hand it the normalised copy)
    grades that -- a correctness property resting on an accident of call order
    is a property nobody can keep.
    """
    raw_body = raw if isinstance(raw, dict) else {}
    # STRIP the host's keys down to the schema BEFORE anything is derived from
    # them. :data:`DEFAULTS_KEY` is the reason: it is this module's own OUTPUT
    # channel and :func:`defaults_used` short-circuits on it, so a host that
    # sends ``{"defaults_used": []}`` ERASED the disclosure sentence, and one
    # that sends ``{"defaults_used": ["goal", "depth"]}`` FABRICATED defaults
    # for fields the tester had set -- host control of constraint 2 in BOTH
    # directions, demonstrated by executing the shipped code, not inferred.
    # The intake packet's ``response_schema`` does NOT close this: a
    # response_schema is a REQUEST TO A MODEL, not an enforcement boundary,
    # and treating it as one is exactly how this reopened from the input side.
    # The schema keys are the only keys; everything else, this one included,
    # is dropped here, and the verdict recorded below is the SERVER's own.
    body = _schema_only(raw_body)
    terms = defaults()
    # The goal's length bound belongs to the explore state, which is the thing
    # that stores it; imported HERE rather than at module scope so this module
    # stays importable from ``explore_runner`` without a cycle.
    from tools.mobile import explore_runner

    terms["goal"] = _one_line(body.get("goal"), explore_runner.MAX_GOAL_CHARS)
    terms["depth"] = _choice(body.get("depth"), DEPTHS, "both")
    scope = body.get("scope")
    scope = scope if isinstance(scope, dict) else {}
    terms["scope"] = {
        "include": _items(scope.get("include")),
        "exclude": _items(scope.get("exclude")),
    }
    terms["destructive"] = _choice(body.get("destructive"), DESTRUCTIVE, "none")
    budget = body.get("budget")
    budget = budget if isinstance(budget, dict) else {}
    terms["budget"] = {
        "steps": max(0, _int(budget.get("steps"), 0)),
        "minutes": max(0, _int(budget.get("minutes"), 0)),
    }
    terms["stop_on"] = _choice(body.get("stop_on"), STOP_ON, "budget")
    terms[DEFAULTS_KEY] = defaults_used(body)
    terms[REJECTED_KEY] = _rejected(body)
    return terms


def parse(text: object, goal: str = "") -> dict:
    """The HOST-SUBMITTED charter string -> ``{"error", "content", "raw"}``.

    Treated as untrusted input, the shape ``tools/jira_mcp.py`` uses: size
    capped, ``json.loads`` only -- never ``eval``, never a literal parser --
    and every field coerced by :func:`normalize`.

    *goal* pre-answers the first question when the caller already has one, so a
    start that carries ``goal=`` runs under the default charter instead of
    being sent back for an intake it does not need.
    """
    body: object = {}
    raw = text if isinstance(text, str) else ""
    if raw.strip():
        if len(raw.encode("utf-8", "ignore")) > MAX_CHARTER_BYTES:
            return {
                "error": (
                    "That charter is larger than this server will read ("
                    + str(MAX_CHARTER_BYTES)
                    + " bytes). Nothing was started -- send the goal, the "
                    "depth, the scope, the destructive setting and the budget, "
                    "and leave the rest out."
                ),
                "content": None,
            }
        try:
            body = json.loads(raw)
        except (TypeError, ValueError, OverflowError) as exc:
            return {
                "error": (
                    "That charter is not readable as JSON ("
                    + str(exc)[:120]
                    + "). Nothing was started."
                ),
                "content": None,
            }
    supplied = _schema_only(body)
    terms = normalize(supplied)
    if not terms["goal"]:
        supplied["goal"] = goal
        terms = normalize(supplied)
    # THE RAW (schema-stripped) BODY TRAVELS WITH THE TERMS, under ``raw``.
    # :func:`questions_for` must judge what the tester actually SENT:
    # normalisation fills every field, so a NORMALISED charter reads as four
    # of five ANSWERED and the intake asks ONE question while silently
    # defaulting the rest -- measured, and the reason this key exists. The two
    # keys are not interchangeable: ``content`` is the run's terms, ``raw`` is
    # the evidence of what was answered.
    return {"error": None, "content": terms, "raw": supplied}


def questions_for(charter: object) -> tuple:
    """The questions still outstanding, NEVER more than :data:`MAX_QUESTIONS`.

    Outstanding is judged by :func:`_unset` against what the caller actually
    sent, so a field answered WITH its default value is not asked again.

    **HAND THIS THE RAW CHARTER, NEVER A NORMALISED ONE.** Measured: this
    function returns FIVE questions for ``{}`` and ONE for ``normalize({})``,
    because normalisation fills every field and four of the five then read as
    ANSWERED. Its only production caller,
    ``agents.mobile_run.build_charter_intake``, passed the normalised charter,
    so a tester with no charter was asked ONE question and the other four were
    silently defaulted -- the absence of the feature wearing the feature's
    name. The function itself was graded (M2d) and the WIRING was not; the
    seam is now graded by
    ``test_an_empty_intake_asks_all_five_questions_through_the_builder``
    (mutant M1c), which asserts ``== 5`` and not ``<= 5``, because an upper
    bound is satisfied by one and that is exactly how this hid.

    The slice is the cap made unbypassable: a caller cannot ask for six by
    handing this an emptier charter. ``QUESTIONS`` has exactly five entries
    today, so the slice is inert in every state the shipped module can reach;
    the only world in which it binds is one where someone appends a SIXTH
    question, and ``test_the_slice_still_holds_with_a_sixth_question`` builds
    exactly that world by monkeypatching ``QUESTIONS`` to six entries (mutant
    M1b). Without that pin nothing would grade this line -- deleting the slice
    survives the suite, measured.
    """
    body = charter if isinstance(charter, dict) else {}
    out = [question for question in QUESTIONS if _unset(body, question["field"])]
    return tuple(out[:MAX_QUESTIONS])


def defaults_used(charter: object) -> list:
    """The fields the tester did not set, in schema order. ONE producer.

    Given a RAW charter this derives the verdict with :func:`_unset`. Given a
    NORMALISED one it returns the verdict :func:`normalize` already recorded --
    the same value, not a second derivation, because the normalised charter no
    longer carries the evidence the derivation needs.

    The report names these. ``stop_on`` can appear here though no question ever
    asked it, and that is the point: the cap on questions is paid for by naming
    what was assumed.
    """
    body = charter if isinstance(charter, dict) else {}
    blank = defaults()
    recorded = body.get(DEFAULTS_KEY)
    if isinstance(recorded, (list, tuple)):
        return [str(field) for field in recorded if str(field) in blank]
    return [field for field in blank if field != "goal" and _unset(body, field)]


def stop_on(charter: object) -> str:
    """The charter's stop condition, defaulted. One reader's worth of coercion."""
    body = charter if isinstance(charter, dict) else {}
    return _choice(body.get("stop_on"), STOP_ON, "budget")


def guard_policy(destructive: object) -> str:
    """``destructive`` -> :data:`PAUSE` or :data:`REFUSE`. The WHOLE decision.

    ``none`` asks to REFUSE: the tester said this run changes nothing, so a
    guard hit should end the attempt rather than open a conversation.
    ``reversible`` and ``allowed`` ask to PAUSE, which is what the lane does
    today.

    **The refuse IS implemented, since step 5.** ``session._submit_explore``
    reads this function for the run's own charter and sets
    ``executor.Context.guard_refuses``; at the one guard-hit site a refusing
    run gets ``executor.GUARD_DETAIL_REFUSED`` and a TERMINAL status instead
    of the tester pause, and ``session`` records the stop -- so neither the
    reply nor the report can imply the run continued.

    What this function does NOT do, stated because a reader will assume it
    does: it does not widen the destructive lexicon, it does not decide
    WHETHER an action is stopped -- the guard alone does that, unchanged --
    and no value it returns lets an action through. It decides only what
    happens AFTER a hit.
    """
    return REFUSE if _choice(destructive, DESTRUCTIVE, "none") == "none" else PAUSE


def budget_cap(charter: object) -> tuple:
    """``(steps, seconds)`` the tester ASKED for; ``0`` where they asked for none.

    The ONE producer of the requested numbers, read by both the initial clamp
    (:func:`budget_for`) and the EXTENSION clamp in
    ``explore_runner.apply_turn_result``. Two derivations of "what did the
    charter ask for" would drift, and the extension site is exactly where the
    first one was missing.
    """
    body = charter if isinstance(charter, dict) else {}
    budget = body.get("budget")
    budget = budget if isinstance(budget, dict) else {}
    steps = max(0, _int(budget.get("steps"), 0))
    minutes = max(0, _int(budget.get("minutes"), 0))
    return steps, minutes * 60


def budget_for(charter: object, max_steps: int, max_seconds: int) -> tuple:
    """``(steps, seconds)`` for this charter, clamped DOWNWARD only.

    A charter can shorten a run and can never lengthen one: the lane's own
    ceilings stay the ceilings, and a tester asking for ten thousand steps gets
    the lane's maximum. Zero -- the default -- means "the lane's own budget".

    This is the START of a run. A run can also be EXTENDED mid-flight, which is
    a SECOND writer of the same two numbers; it re-clamps through
    :func:`budget_cap` at its own site, because this function is not on that
    path and cannot bound it.
    """
    steps, seconds = budget_cap(charter)
    out_steps = max(1, min(steps, int(max_steps))) if steps > 0 else int(max_steps)
    out_seconds = (
        max(60, min(seconds, int(max_seconds))) if seconds > 0 else int(max_seconds)
    )
    return out_steps, out_seconds

def depth_directive(charter: object) -> str:
    """What this run's ``depth`` asks the model to ATTEMPT. ONE producer.

    The turn packet carries this sentence and nothing else in this server
    derives it. NOT a tactics catalogue -- see :data:`DEPTH_DIRECTIVES`.
    """
    body = charter if isinstance(charter, dict) else {}
    return DEPTH_DIRECTIVES[_choice(body.get("depth"), DEPTHS, "both")]


def scope_lines(charter: object) -> dict:
    """``{"include": [...], "exclude": [...]}``, coerced. ONE producer.

    Read by the packet builder and by :func:`scope_verdict`, so the lines the
    model is TOLD about and the lines the server JUDGES against are the same
    list. Two derivations would drift, and the drift is invisible from either
    end: the model would be told one scope while the report recorded another.
    """
    body = charter if isinstance(charter, dict) else {}
    scope = body.get("scope")
    scope = scope if isinstance(scope, dict) else {}
    return {
        "include": _items(scope.get("include")),
        "exclude": _items(scope.get("exclude")),
    }


def scope_sentence(charter: object) -> str:
    """The scope as one plain phrase, for the PACKET.

    Shares :func:`_scope_phrase` with the report sentence, so the tester and
    the tester's model read the same scope in the same words.
    """
    body = charter if isinstance(charter, dict) else {}
    return _scope_phrase(body.get("scope"))


def _named(body: object, fields: tuple) -> list:
    """The ALLOWLISTED string fields of one record. ONE helper, both levels.

    Screen and element are read through the same function so the two lists
    cannot acquire different rules: :data:`SCOPE_SCREEN_FIELDS` and
    :data:`SCOPE_ELEMENT_FIELDS` are the whole difference between them.
    """
    record = body if isinstance(body, dict) else {}
    out = []
    for key in fields:
        value = record.get(key)
        if isinstance(value, str) and value:
            out.append(value)
    return out


def _screen_terms(screen: object) -> str:
    """WHAT THE TESTER CAN SEE on this screen, lower-cased and bounded.

    An ALLOWLIST, not every string the record carries -- see
    :data:`SCOPE_SCREEN_FIELDS` for the measured failure that decided it and
    for the honest statement of what the key-agnostic form bought and cost.
    In one line: ``screen_id``, ``hash``, ``package``, ``dialog_package``,
    ``activity``, ``rid`` and ``cls`` are IDENTIFIERS the tester never wrote,
    and a scope line matching one of those identifies nothing.

    The residual risk is stated rather than hidden: a provider that renames
    ``text``/``desc`` empties the haystack, and the verdict then fails
    ``in_scope``/``not_confirmed`` -- the direction that explores a screen it
    might have avoided, never the direction that abandons the whole app.
    """
    body = screen if isinstance(screen, dict) else {}
    parts = _named(body, SCOPE_SCREEN_FIELDS)
    for element in list(body.get("elements") or []):
        parts.extend(_named(element, SCOPE_ELEMENT_FIELDS))
    return " ".join(parts).lower()[:MAX_SCOPE_MATCH_CHARS]


def _needle(line: object) -> str:
    return " ".join(str(line or "").split()).lower()[:MAX_SCOPE_ITEM_CHARS]


def scope_verdict(charter: object, screen: object) -> dict:
    """Is THIS screen inside the charter's scope? ONE producer, THREE answers.

    ``{"state", "matched", "note"}``, where ``state`` is :data:`SCOPE_IN`,
    :data:`SCOPE_OUT` or :data:`SCOPE_UNCONFIRMED`.

    **Every answer identifies POSITIVELY.** ``out_of_scope`` means an EXCLUDE
    line was FOUND in what the tester can SEE on the screen (the allowlisted
    fields of :func:`_screen_terms`, never an identifier) -- never "no
    include matched", which is a different fact and has its own name. That third answer is what keeps the
    rule honest: a tester writes "Checkout" and the screen says "Basket", and
    a two-valued predicate would either explore a flow it was told to avoid or
    declare the whole app out of bounds. The undeterminable row is the one
    that decides the design, so it gets its own value and its own sentence.

    This server does NOT stop a run on ``out_of_scope``: the visit is RECORDED
    (``explore_runner.next_turn`` appends it to ``state["off_charter"]``) and
    the model is told to leave. Nothing grades a scope-driven stop, because
    nothing implements one -- follow-up.
    """
    lines = scope_lines(charter)
    haystack = _screen_terms(screen)
    for line in lines["exclude"]:
        needle = _needle(line)
        if needle and needle in haystack:
            return {"state": SCOPE_OUT, "matched": needle, "note": OFF_CHARTER_NOTE}
    if not lines["include"]:
        return {"state": SCOPE_IN, "matched": "", "note": ""}
    for line in lines["include"]:
        needle = _needle(line)
        if needle and needle in haystack:
            return {"state": SCOPE_IN, "matched": needle, "note": ""}
    return {"state": SCOPE_UNCONFIRMED, "matched": "", "note": UNCONFIRMED_NOTE}


def record_off_charter(visits: object, entry: object) -> list:
    """Append ONE off-charter visit -- DEDUPLICATED and BOUNDED. ONE producer.

    **The identity rule: one record per ``(screen_id, matched)`` pair.** What
    the tester needs to know is that the run TOUCHED an excluded flow and
    which line matched it; how many consecutive turns the model lingered there
    is a property of the model's next move, not of the charter, and recording
    it fills the manifest with copies of one fact. Measured: without this the
    same screen was recorded on turn 1 and again on turn 2.

    This is also the ONE reader of :data:`MAX_OFF_CHARTER_RECORDS` -- the cap
    is applied HERE rather than at the call site, so the bound and the
    identity rule cannot be applied in one place and forgotten in another.
    """
    out = [dict(item) for item in list(visits or []) if isinstance(item, dict)]
    body = entry if isinstance(entry, dict) else {}
    key = (str(body.get("screen_id") or ""), str(body.get("matched") or ""))
    seen = {
        (str(item.get("screen_id") or ""), str(item.get("matched") or ""))
        for item in out
    }
    if key not in seen:
        out.append(dict(body))
    return out[-MAX_OFF_CHARTER_RECORDS:]


def _scope_phrase(scope: object) -> str:
    body = scope if isinstance(scope, dict) else {}
    include = _items(body.get("include"))
    exclude = _items(body.get("exclude"))
    if not include and not exclude:
        return "the whole app"
    said = []
    if include:
        said.append("inside " + ", ".join(include))
    if exclude:
        said.append("staying out of " + ", ".join(exclude))
    return "; ".join(said)


def describe_terms(charter: object) -> str:
    """The run's TERMS as one plain sentence, defaults NAMED. ONE producer.

    Returns ``""`` for a run with no charter -- every run recorded before this
    existed -- so the page it goes on renders exactly as it did.

    **Order is load-bearing.** The report clips this string, and the scope can
    fill it on its own (``MAX_SCOPE_ITEMS`` x ``MAX_SCOPE_ITEM_CHARS``), so
    every clause that DISCLOSES something -- which defaults were assumed, which
    stop condition is not detected -- comes AHEAD of the clippable content
    it concerns. A disclosure that a long scope can push past the clip is a
    disclosure that is not made.

    Ordering alone does not deliver that, and the difference matters: the
    charter clause sits AFTER the budget clause, so it survives the clip only
    because every clause ahead of it is BOUNDED. The scope is
    bounded by ``MAX_SCOPE_ITEMS``/``MAX_SCOPE_ITEM_CHARS`` and the budget
    figures by :data:`MAX_BUDGET_UNITS` in :func:`_int` -- without which a
    caller sending ``1e300`` writes a 301-digit number into that clause and
    pushes the disclosure off the page (mutant M12). ONE unbounded clause
    anywhere ahead of a disclosure defeats the whole ordering, so a new clause
    owes a bound.

    **Voice is load-bearing too, and it follows the WIRING.** ``depth``,
    ``scope`` and ``destructive`` now steer the run, so they are rendered in
    the present indicative and the sentence says a resume explores under the
    same terms. What is STILL unwired -- ``coverage_plateau`` -- keeps the
    recorded-but-not-yet-detected voice. A disclosure and the pins that grade
    it are ONE artifact: changing this text without moving those pins is how a
    stale claim survives its own fix.

    The defaults clause is not decoration. A default nobody is told about is a
    run whose terms cannot be reconstructed afterwards, which is the same
    failure as not recording them at all.
    """
    if not isinstance(charter, dict) or not charter:
        return ""
    terms = normalize(charter)
    said = ""
    # :func:`normalize` is the strip point, so a charter that reached
    # persistence carries the SERVER's verdict and never a host-supplied list.
    # READ OFF ``charter``, THE ARGUMENT AS GIVEN -- never off ``terms``.
    # :func:`normalize` is NOT idempotent on its own output (see its
    # docstring): re-normalising strips ``DEFAULTS_KEY`` and re-derives it
    # against a fully filled body, so ``defaults_used(terms)`` is ALWAYS [] and
    # the disclosure clause silently disappears. That is mutant M11, and it is
    # named here because this line's correctness otherwise rests on an accident
    # of call order that no reader could be expected to preserve.
    # REFUSE BY NAME, AHEAD OF EVERY CLIPPABLE CLAUSE. A coercer that replaces
    # an out-of-enum answer with the default REDUCES what was asked for, and
    # the invariant is that such a site refuses by name in the same reply. Read
    # off ``charter``, the argument as given, for the M11 reason: re-normalising
    # strips the recorded verdict. The clause is BOUNDED -- at most five
    # entries, each value trimmed to 40 characters -- because one unbounded
    # clause ahead of a disclosure defeats the whole ordering.
    for spoiled in rejected_fields(charter):
        said += (
            str(spoiled.get("field"))
            + " was sent as "
            + str(spoiled.get("sent"))
            + ", which is not one of "
            + ", ".join(str(choice) for choice in (spoiled.get("choices") or []))
            + ", so the server used "
            + str(spoiled.get("used"))
            + ". "
        )
    assumed = defaults_used(charter)
    if assumed:
        said += (
            "The tester did not set "
            + ", ".join(assumed)
            + ", so the server used its own default for "
            + ("each" if len(assumed) > 1 else "it")
            + ". "
        )
    if terms["stop_on"] not in WIRED_STOPS:
        said += (
            terms["stop_on"] + " is recorded but not yet detected, so this run "
            "stopped on its budget instead. "
        )
    if guard_policy(terms["destructive"]) == REFUSE:
        said += (
            "This charter asks for a guard hit to END the attempt, and it "
            "does: a stopped action ends this run by name rather than pausing "
            "for the tester. "
        )
    budget = terms["budget"]
    spend = (
        (
            str(budget["steps"]) + " steps"
            if budget["steps"]
            else "the lane's own step budget"
        )
        + " and "
        + (
            str(budget["minutes"]) + " minutes"
            if budget["minutes"]
            else "the lane's own time budget"
        )
    )
    # THE BUDGET THE RUN HAD, not only the one it was ASKED for. ``new_state``
    # clamps the ask DOWNWARD through :func:`budget_for`, so a charter asking
    # for 100 steps and 60 minutes runs on the lane's 30 turns and 20 minutes
    # -- measured. This SENTENCE is a second consumer of the same fact and it
    # named the ask alone, which is a false statement about the run the tester
    # just had. M6/M6b grade the clamp in ISOLATION, which is exactly why the
    # sentence stayed wrong. Both numbers are rendered: either alone is the
    # worse artifact.
    from tools.mobile import explore_runner

    asked_steps, asked_seconds = budget_cap(terms)
    ran_steps, ran_seconds = budget_for(
        terms, explore_runner.MAX_TURNS, explore_runner.DEADLINE_S
    )
    clamped = []
    if asked_steps and ran_steps < asked_steps:
        clamped.append(str(ran_steps) + " turns")
    if asked_seconds and ran_seconds < asked_seconds:
        clamped.append(str(ran_seconds // 60) + " minutes")
    if clamped:
        # ONLY THE AXIS THAT ACTUALLY CLAMPED. Naming BOTH whenever EITHER
        # clamped printed the TESTER'S OWN number back at them as "the lane's
        # ceiling": measured, an ask of 100 steps and 5 minutes rendered
        # "clamped to the lane's ceiling of 30 turns and 5 minutes" while the
        # lane's time ceiling is 20 minutes and the run got the tester's 5 --
        # a false sentence in the artifact that reconstructs the run, and a
        # reintroduction of the very class the ask-versus-had fix above
        # closed. Graded by M6e against a FOUR-FIXTURE MATRIX (steps-only,
        # minutes-only, both, neither) built BEFORE this rule, because
        # fixture diversity and not mutation count is what grades it.
        spend += ", clamped to the lane's ceiling of " + " and ".join(clamped)
    said += "Budget " + spend + "; stopping on " + terms["stop_on"] + ". "
    # THE DISCLOSURE THESE THREE FIELDS USED TO CARRY IS GONE, because the gap
    # it described is gone: the turn packet carries the depth directive and
    # the scope lines, and the destructive policy decides what happens after a
    # guard hit. A disclosure that outlives the gap it described is the same
    # defect as one that preceded the fix, so it was retired in the SAME
    # commit that wired them. What is still genuinely unwired keeps its own
    # sentence -- see the ``coverage_plateau`` clause above, untouched.
    said += (
        "Charter: depth "
        + terms["depth"]
        + " -- the turn packet asks for that work by name; destructive "
        + terms["destructive"]
        + (
            " (a guard stop ENDS the run)"
            if guard_policy(terms["destructive"]) == REFUSE
            else " (a guard stop pauses the run for the tester)"
        )
        + "; scope "
        + _scope_phrase(terms["scope"])
        + " -- the packet carries those lines, and a screen matching an "
        "excluded flow is RECORDED as an off-charter visit, though nothing "
        "stops the run on one. A resume reads these back and explores under "
        "them."
    )
    return said
