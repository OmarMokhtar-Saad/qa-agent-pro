"""Mobile session core: states, lane names and the call-budget scalars. Cut verbatim from tools/mobile/session_core.py (C5 w17 sessA)."""

from __future__ import annotations

STATE_NEEDS_FLAG = "needs_flag"
STATE_PROVISIONING = "provisioning"
#: ``ensure_run_device`` only: the run's device is gone and the AVD it came from
#: no longer exists, so there is nothing to re-boot. Never written to a manifest;
#: its one consumer, ``mcp_handlers._mobile_resume_device_stage``, answers it
#: with the no-AVD setup guide.
STATE_NO_AVD = "no_avd"
STATE_BOOTING = "booting"
STATE_NEEDS_APP = "needs_app"
STATE_PREFLIGHT = "preflight"
STATE_MENU = "menu"
STATE_RUNNING = "running"
STATE_GATE = "gate"
STATE_REPORT = "report"
STATE_TAKEN_OVER = "taken_over"
STATE_BUSY = "busy"

#: An unfinished run whose lease heartbeat stopped (2026-09-03, D7). NOT a
#: takeover: nobody else holds it, the chat that did simply went away -- so the
#: tester is told it can be picked up rather than that it is gone. Distinct
#: from STATE_TAKEN_OVER, which names a run another chat IS driving.
STATE_ABANDONED = "abandoned"

#: Every state a handler can be in, in flow order. Also the set the tests assert
#: against, so a state that silently stops being reachable fails the suite.
STATES: tuple[str, ...] = (
    STATE_NEEDS_FLAG,
    STATE_PROVISIONING,
    STATE_BOOTING,
    STATE_NEEDS_APP,
    STATE_PREFLIGHT,
    STATE_MENU,
    STATE_RUNNING,
    STATE_GATE,
    STATE_ABANDONED,
    STATE_REPORT,
    STATE_TAKEN_OVER,
    STATE_BUSY,
)

#: The longest ONE tool call may run before it hands back a pointer instead of
#: an answer. Clients kill a tool call at around 50 seconds and a killed call is
#: indistinguishable from a broken server, so this bound is STRUCTURAL: it is
#: passed in, checked at the two places that start slow device work, and never
#: asserted against wall-clock in a test -- a test that measured elapsed time
#: here would be asserting below its own noise floor.
CALL_BUDGET_S = 45


#: The tail of :data:`CALL_BUDGET_S` reserved, once, for one best-effort
#: screen capture when a busy reply is about to go out with nothing in it.
#: ``next_packet`` spends it on :func:`_final_packet` instead of starting the
#: slow relaunch/foreground-poll path this reserve exists to avoid overrunning.
PACKET_RESERVE_S = 8


LANE_SUITE = "suite"
LANE_EXPLORE = "explore"

_RUN_PREFIX = "mrun-"


# What reading and walking a stored case dump can raise: a file that will not
# read, or a record of an unexpected shape.
_CASE_STORE_ERRORS = (OSError, ValueError, TypeError, AttributeError, KeyError)
