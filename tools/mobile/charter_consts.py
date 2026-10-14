"""Bounds, scope-screen allowlists and fixed notes of the charter lane.

Split from :mod:`tools.mobile.charter`, which re-exports every name here.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("tools.mobile.charter")


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
