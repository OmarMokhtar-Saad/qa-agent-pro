"""Replay guard vocabulary: the stop wording, hints and sentinels."""

from __future__ import annotations

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
    "Report only what a qa_* observation read. If the server cannot type or "
    "act, stop and name the blocker; no raw adb, no claimed result."
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


#: Assert kinds that name something the screen now SHOWS as a thing: an
#: element, or text that was not there before this turn's action.
NEW_ELEMENT_KINDS = ("element", "new_text")
