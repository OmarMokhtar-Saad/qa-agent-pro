"""Replay engine: the run Context, status constants, per-run state classes and the sleep seam. Cut verbatim from tools/mobile/executor.py (P4.5)."""

from __future__ import annotations

import dataclasses
import logging
import time

from tools.mobile import actions as actions_mod
from tools.mobile import (
    knowledge_limits,
    step_timing,
)

logger = logging.getLogger("tools.mobile.executor")


#: Interruptions a dialog handler may answer in ONE settle. An installer is
#: Update then Done (two); past three the screen is not a dialog sequence the
#: handlers know, so the replay stops and the model decides.
MAX_DIALOG_TAPS = 3

#: Pause between a handler's tap and the re-read, so the dialog can leave.
DIALOG_SETTLE_S = 0.4

MAX_TRACE = 80


STATUS_DONE = "done"
STATUS_NEEDS_MODEL = "needs_model"
STATUS_NEEDS_TESTER = "needs_tester"
STATUS_ERROR = "error"

#: A "done" carrying verdict=pass that recorded no assert_pass and no screen
#: change is not evidence of anything -- see ``_has_verification``.
STATUS_UNVERIFIED = "unverified"

#: How often a polling ``wait`` re-dumps the screen.
#:
#: THE DUMP IS THE RATE LIMITER, not this value. ``uiautomator dump`` blocks
#: until the accessibility layer goes idle, and that wait dominates a poll
#: iteration by a wide margin -- no flag reduces it. So a sleep on top of it
#: bought nothing and lengthened EVERY iteration by its own full value, on a
#: loop that may run until :data:`DEFAULT_WAIT_UNTIL_TEXT_S`. What is left here
#: is only what stops a tight loop against a device that answers instantly,
#: which is a fake in a test rather than a phone.
#:
#: The dump's cost is a DEVICE fact this tree does not own and no number for it
#: is written here; ``tests/live/test_mobile_adb_latency_sweep.py`` measures it
#: and asserts the ordering this reasoning rests on.
WAIT_POLL_S = 0.25

#: The longest :func:`_auto_settle` keeps reading after a screen-changing
#: action whose first post-action read showed NO change (owner decision, batch
#: 3). A slow app paints its next screen after the dump that followed the tap,
#: and that read used to be handed back as ``no_change`` -- so the model tapped
#: again. Bounded, because a tap that really did nothing must still say so.
AUTO_SETTLE_MAX_S = 2.0

#: Screen-changing ops that do NOT auto-settle: a field edit changes one
#: element in place, and polling for a "change" after it would spend the whole
#: bound on every keystroke the IME already confirmed.
NO_AUTO_SETTLE_OPS = frozenset({"type", "fill", "clear"})

#: The longest a legacy ``wait ms`` runs, now that it runs as
#: ``wait_until_changed`` (S17): the condition waits' own ceiling. There is no
#: floor below which a wait is slept instead of polled, and no top-up sleep
#: after an unchanged screen -- every wait ends on its condition or its bound.
#: Nothing overruns the bound: each poll's dump is cut at the deadline.
LEGACY_WAIT_MAX_MS = actions_mod.WAIT_UNTIL_MAX_S * 1000

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
    run_facts: object = None
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


@dataclasses.dataclass(frozen=True)
class _ReplayRun:
    """What one replay carries from step to step: the context, the actions and
    the trace being built. Passed to the step helpers of ``_replay_steps``."""

    ctx: Context
    items: list
    trace: list[dict]


class _StepCursor:
    """Iterates ``(index, action)`` over a list that knowledge plugs may splice
    into mid-run. ``insert`` places steps at the cursor and re-enters there, so
    inserted steps meet the same guards as scripted ones."""

    def __init__(self, items: list) -> None:
        self.items = items
        self.pos = 0
        self.budget = knowledge_limits.PRECONDITION_STEPS * 4

    def __iter__(self) -> "_StepCursor":
        return self

    def __next__(self) -> tuple[int, object]:
        if self.pos >= len(self.items):
            raise StopIteration
        self.pos += 1
        return self.pos - 1, self.items[self.pos - 1]

    def insert(
        self, index: int, steps: object, route_locked: bool, consume: int = 0
    ) -> bool:
        """Splice *steps* before ``index``, replacing the next *consume* scripted
        steps (a shortcut stands in for them); ``False`` (nothing inserted) when
        the run is route-locked, the steps are empty or the per-run budget is spent."""
        new = [st for st in list(steps or []) if st is not None]
        if route_locked or not new or len(new) > self.budget:
            return False
        self.budget -= len(new)
        self.items[index : index + max(0, consume)] = new
        self.pos = index
        return True


@dataclasses.dataclass(frozen=True)
class _Step:
    """One action with the target it resolved to; built before the guard judges it."""

    op: str
    action: object
    entry: dict
    element: object
    scroll_missed: bool


@dataclasses.dataclass
class _ReplayState:
    """What changes from one step to the next: the live screen, the budget
    deadline and the screen content before the last screen-changing action.

    ``baseline_texts`` is ``None`` until one has run, which is the honest answer
    ``new_text`` gives when a script asserts a reply before sending anything."""

    screen: dict | None
    deadline: float
    baseline_texts: set | None = None


def _append(trace: list[dict], entry: dict) -> None:
    """Stamp the action's duration and keep the entry. The private ``_started``
    mark never reaches the trace: it is popped here, on every path."""
    started = entry.pop("_started", None)
    if started is not None:
        entry["ms"] = int(max(0.0, time.monotonic() - float(started)) * 1000)
    if len(trace) < MAX_TRACE:
        trace.append(entry)


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


async def _sleep(seconds: float) -> None:
    """Named so a test can patch it; a real sleep would be the slowest thing here."""
    import asyncio

    await step_timing.timed("wait", asyncio.sleep(seconds))
