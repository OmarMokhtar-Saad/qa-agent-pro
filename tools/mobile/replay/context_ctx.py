"""Replay run Context: everything a replay needs about the device it is driving."""

from __future__ import annotations

import dataclasses
from typing import Any


@dataclasses.dataclass
class Context:
    """Everything a replay needs about the device it is driving."""

    serial: str
    package: str = ""
    # The run's OWN package, which a multi-app hand-over never moves (fix
    # round 3, item 1). `package` above is the app under test NOW and is what
    # every guard compares against; `home_package` is the only app
    # `clear_app_data` may wipe. Empty means "same as `package`".
    home_package: str = ""
    activity: str = ""
    guard_destructive: bool = True
    screen: dict | None = None
    # One tester-supplied value per field, held for THIS call only and never
    # written anywhere. ``case_runner`` fills it from the submit argument.
    tester_inputs: dict | None = None
    # Per-call wall-clock bound for the replay. ``None`` means
    # :data:`SUBMIT_BUDGET_S`; a test shortens it rather than sleeping.
    budget_s: float | None = None
    # Whether an EARLIER unit of the same run already recorded an
    # ``assert_pass``. Defaults False, which is the SCRIPTED lane's rule
    # unchanged: there the case IS the unit that earns the verdict and its own
    # trace must carry the check. The explore lane sets it, because there one
    # turn is one case and the unit that earns a verdict is the RUN -- so the
    # turn that calls ``done`` can never carry the assert an earlier turn made.
    # An explicit input, never a store read: this module drives a device and
    # knows nothing about runs.
    prior_verified: bool = False
    # Whether a script that asserts NOTHING is an omission worth naming. True
    # is the scripted lane: there the case IS the unit that earns the verdict,
    # so a script with no assert has skipped the only thing that could have
    # earned one, and saying so is what stops an empty verdict reading as a
    # pass. The explore lane sets it False, because there one turn is one case
    # and a turn that only taps and types is the NORMAL case -- printing the
    # scripted sentence on the majority path trains the reader to ignore it.
    #
    # A fact about what a trace is EXPECTED to carry, not a lane name: this
    # module still knows nothing about runs, and the default leaves the
    # scripted rule untouched and unweakened.
    asserts_expected: bool = (
        True  # What happens AFTER the destructive guard stops something. False PAUSES
    )
    # for the tester (``needs_tester``), which is today's behaviour and the
    # default, so the SCRIPTED lane is unchanged byte-for-byte. True ENDS the
    # attempt, which is what a charter saying ``destructive: none`` asks for;
    # the explore lane's own submit seam sets it from the charter's guard
    # policy. NOTE the wording: ``tests/mobile/test_mobile_charter.py``
    # forbids the dotted form in this file, and that pin is PROSE-SENSITIVE
    # -- it reads the source text, so a comment naming the attribute trips
    # it exactly as an import would. The property it protects (this module
    # depends on no charter module) is genuinely satisfied: nothing here
    # imports one or reads an attribute off one.
    #
    # It decides NOTHING about what is stopped. The lexicon, the actuated-node
    # judging and the screen-level questions are untouched, and no value here
    # lets an action through the guard.
    guard_refuses: bool = False
    # ONE control stop the tester confirmed, for THIS submission only. Spent
    # by the first control the guard stops (see ``_consume_confirm``), so a
    # second destructive op in the same script stops again. It never clears
    # a screen-level stop, and ``guard_refuses`` outranks it.
    confirm_destructive: bool = False
    # Whether the explore turn right before this one failed to type or moved
    # nothing. An explicit input like ``prior_verified``: set by the explore
    # lane's submit seam, False on the scripted lane.
    prior_turn_blocked: bool = False
    # The fingerprint of the control THIS run's LAST turn stopped on --
    # ``{"term": ..., "op": ..., "node": ...}`` -- or ``{}`` if that turn
    # recorded no stop. ``None`` (the default) means UNTRACKED: every direct
    # ``Context(...)`` unit test that never sets this keeps today's lenient
    # behaviour, where any control stop's own confirm releases it. Only
    # ``session.submit`` sets a dict, and only there does ``_consume_confirm``
    # compare against it -- a confirm naming a different op, or arriving
    # before any stop was recorded, then refuses by name and stays unspent.
    prior_guard_stop: dict | None = None
    # This app's saved notes for THIS replay: ``{package, guards, counts,
    # events, spent_ms}``. ``None`` until ``replay`` loads it (once per replay);
    # a direct ``Context(...)`` test may preset it. Never written by a guard
    # except through ``_apply_knowledge``.
    knowledge: dict | None = None
    # Per-app knowledge run facts (contract 1.4/1.6): the run's id and lane, the
    # installed app version, the run env, the bounded in-memory event buffer the
    # hook plugs fill, and the cached RunFacts. All optional.
    run_id: str | None = None
    lane: str | None = None
    app_version: str = ""
    env: dict = dataclasses.field(default_factory=dict)
    kbuf: list = dataclasses.field(default_factory=list)
    run_facts: Any = None
    # The steps still to run, current step first; set before each ``pre_step``
    # so the shortcut plug can match the model's upcoming steps.
    script_ahead: list = dataclasses.field(default_factory=list)
    # Non-empty when the QA keyboard could not be set up and typing goes through
    # `adb shell input text` instead (R1d). Holds the reason. Set by the replay
    # itself or by the case runner; empty (the default) is the normal path.
    typing_fallback: str = ""
    # Set by a wait that ended EARLY because something new took the screen
    # (a dialog, a call, an error text, a device that stopped answering): the
    # "interrupted: <what>" sentence. Read and cleared by the `wait` branch.
    wait_interruption: str = ""
    # S10 saved route (PROVISIONAL): the screen_id each step must START on, in script order.
    # Empty = not a route replay. Set by session.submit; read ONLY by _route_expected.
    route_expect: tuple = ()
    # Whether the last screen this device read may stand for the NEXT call's
    # first read (``_take_carried``). False is today's behaviour: every call
    # starts with its own dump. The session seam sets it.
    carry_screen: bool = False
    # Packages this step expects in front besides the run's own app (a launch
    # into the App Tester, an installer). A screen of one is NOT 'left the
    # app'. Empty (the default) is today's behaviour. Set by the caller.
    expect_apps: tuple = ()
    # Per-step identity gate of a batched replay: ``async (index, screen) ->
    # (screen, why)``. Run on the screen in hand before step *index*; a non-empty
    # ``why`` ends the script there with nothing actuated. ``None`` = no gate.
    step_gate: Any = None
