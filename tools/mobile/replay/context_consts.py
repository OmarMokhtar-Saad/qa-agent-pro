"""Replay constants: logger, dialog/trace bounds, status codes and wait budgets."""

from __future__ import annotations

import logging

from tools.mobile import actions as actions_mod

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

#: The longest an explore turn waits for the screen to change (S3). The turn
#: ends and hands the model the screen; a 10s wait for a change that is not
#: coming only delays that. Scripted runs keep the model's own ``ms``.
EXPLORE_CHANGED_WAIT_MAX_MS = 3000

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
