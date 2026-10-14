"""Process-wide MCP server state: logger, server name, client identity, drift and
duplicate-call constants. Defined ONCE here; readers take the dicts and
constants from this module (rebinding patches target it)."""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger("qa_agents.mcp")

SERVER_NAME = "qa-agent-pro"


# Latest MCP client identity from the initialize handshake (clientInfo),
# used to tag telemetry events with the host editor (cursor / claude-code).
_CLIENT = {"name": "", "version": ""}


# ---- in-flight accounting for the drift restart (F1) -----------------------
# A hot restart must never land while a tool body is executing. _tracked is the
# single funnel every registered tool passes through, so the counter lives HERE
# rather than in a second wrapper.
#
# _DRAIN_IDLE_S additionally demands a quiet gap measured from the last tool to
# FINISH. 120s is chosen from the run this fix came from: the eight
# qa_submit_category calls arrived 37-41s apart, so a shorter gap would
# routinely exit in the middle of a tester's session. It still costs at most one
# deferral against the 15-minute tick.
_DRAIN_IDLE_S = 120.0
_DEFER_WARN_EVERY = 20

# Exit code for a DELIBERATE drift restart. The dist launcher's supervisor
# matches this exact number in _pump_child_out to tell a version reload apart
# from a crash (LAUNCHER_TEMPLATE.DRIFT_EXIT_CODE in scripts/build_dist.py --
# the two literals MUST stay equal; tests/test_launcher_drift_exit.py asserts
# it, because the launcher is generated source and cannot import from here).
# 2026-08-09: without that match every release logged one
# "MCP server exited unexpectedly" per connected client, which MCP clients
# render as an error.
#
# 0 is deliberately NOT used: under the dist the editor never sees THIS
# process's exit code -- it sees the launcher's. This code is a private
# protocol with the supervising launcher, which must respawn and replay the
# handshake here, and 0 is indistinguishable from a normal shutdown.
DRIFT_RESTART_EXIT_CODE = 86
# Every client sharing one install detects the same peer update on the same
# tick, so they would otherwise all exit and respawn in the same second.
_DRIFT_EXIT_JITTER_S = 3.0
_INFLIGHT: dict = {"n": 0, "last_finish": 0.0}
_INFLIGHT_LOCK = threading.Lock()

# P1-5 (Air onboarding fix): a duplicate call landing within this window --
# a host retry, a double-click in a chat UI -- returns the cached result
# instead of re-running the tool. Deliberately NOT inside `_tracked()`: that
# function's docstring says it is structurally denied an `args` parameter so
# a payload can never leak through the one seam every tool shares, so this
# cache has to live at each call site instead.
_RECENT_CALL_TTL_S = 5.0
# qa_generate_test_cases gets a longer window: in the Air run the duplicate
# arrived ~6s after a 17s call had FINISHED, which 5s would miss. This is a
# duplicate-call guard, NOT the removed 24h finished-suite stop: 30s, same
# args only, and only a reply that created a prep is replayed.
_GENERATE_DUP_WINDOW_S = 30.0
_RECENT_CALLS: dict = {}
_RECENT_CALLS_LOCK = threading.Lock()


# Public seams: readers resolve the live dict at call time so a test can swap it.
def recent_calls() -> dict:
    return _RECENT_CALLS


def client_info() -> dict:
    return _CLIENT
