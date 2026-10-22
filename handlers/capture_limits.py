"""Caps and in-process state for the capture tray and the carry-forward shelf.

The ONE home of every cap and state dict. Consumers read them at CALL time as
``from handlers import capture_limits as _limits`` / ``_limits.NAME`` so a test
that patches a cap here bites; the state dicts are mutated in place, never rebound.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Device screenshot tray (qa_capture_screens -> qa_prepare_test_cases)
#
# An MCP tool result cannot be handed to another tool call, and raw PNG bytes
# must never enter the prep store (which is JSON) -- so captured screens are
# stashed in this in-process tray keyed by capture_id and READ by
# handle_prepare_test_cases(capture_ids=[...]). The MCP server is ONE
# long-running stdio process, so the dict survives between the two tool calls.
# Deliberately NOT persisted: bytes on disk are a liability, and
# `_budget_keep_flags` already bounds what can ride on a reply.
#
# READ-then-DROP, never consume-on-read. A Jira source ALWAYS costs at least two
# prepare calls -- the first returns the fetch DIRECTIVE -- so popping the tray
# while reading it destroyed every screen on the round that fetched nothing, and
# the follow-up call (the one that matters) saw only missing ids. That is an
# endless capture -> directive -> nudge -> capture loop. So peek_captures is
# read-only and _drop_captures runs ONLY immediately before a payload is
# returned; a failed prepare therefore leaves the ids usable for the retry, and
# _capture_retry_hint says so.
#
# ACCEPTED LIMITS (documented, deliberately not fixed here): a tray entry is not
# bound to a source or a prep, so any prepare call in this process could consume
# any pending capture_id -- acceptable because this is a single-tester stdio
# process and the ids are 96-bit random. And the TTL has no timer: it is
# enforced whenever the tray is TOUCHED (every stash, every peek -- and the peek
# runs on every prepare, capture_ids or not), so at most _CAPTURE_TRAY_MAX
# screens can sit resident between two tool calls.
# --------------------------------------------------------------------------- #

_CAPTURE_TRAY: dict = {}
_CAPTURE_TRAY_TTL_S = 1800
_CAPTURE_TRAY_MAX = 24


# WHY an id the tester was handed no longer resolves -- keyed by id, written by
# EVERY evictor (tray count, tray bytes, shelf count, shelf bytes, a revive that
# did not fit) and read at the LAST consumer, peek_captures, which puts the
# reason beside the id in `missing`. Before 2026-09-06 every one of those
# outcomes reached the tester as the same generic "unknown or expired id":
# measured, two consecutive qa_capture_screens calls of 6 x 3 MiB each reported
# "Captured 6" with no drop note, while the byte sweep -- oldest first -- had
# silently killed 2 of the FIRST call's ids, and a prepare with all six was
# grounded on four screens. A TTL expiry is not recorded here: "expired" is the
# generic message's own honest case. Bounded by count and by the tray TTL, so
# it cannot outlive the ids it explains.
_CAPTURE_EVICTIONS: dict = {}
_CAPTURE_EVICTIONS_MAX = 200


#: Described missing ids shown by name in a reply before the rest are counted.
_MAX_MISSING_SHOWN = 8


# How many screens ONE qa_capture_screens call may take. Separate from
# _MAX_ELICIT_ROUNDS, which bounds DIALOGS rather than captures.
_CAPTURE_COUNT_MAX = 12


# K2 (2026-08-10): the ticket's OWN image labels, shelved by image-gate BEAT 2 so
# qa_capture_screens can name the screens the tester is about to take instead of
# asking them a third question about the same screenshot.
#
# Single slot, last-write-wins, <=8 short strings, no bytes -- it cannot grow.
# POPPED on first read and stamped with the source it came from, because
# handle_capture_screens has no ticket context of its own (that is the whole reason
# this exists): the key gives DISCLOSURE, not prevention. The first capture after a
# beat-2 prepare consumes these labels whichever ticket the tester has moved on to,
# so the reply NAMES the source and the pop bounds it to one capture.
_TICKET_IMAGE_LABELS: dict = {"labels": [], "at": 0.0, "source": ""}


# --------------------------------------------------------------------------- #
# Carry-forward shelf (2026-08-09)
#
# A prepare that SHIPS its screens drops them from the tray, so BOTH ways a
# tester can come back for the same source lost the grounding silently:
#
#   * a RE-PREPARE with no images (observed live 2026-08-09 -- a prep with 2
#     captured screens at 15:01, a second prep for the SAME source with 0 at
#     15:04, 88 cases written from the imageless one), and
#   * the RE-SENT ids the tool docstrings explicitly ask for, which resolved to
#     nothing and produced only a generic "unknown or expired id" note.
#
# Dropped entries are therefore MOVED here rather than freed, and can be revived
# under their ORIGINAL ids. The tray's own contract is unchanged: a dropped id no
# longer resolves through peek_captures. Bounded on BOTH axes (count and total
# bytes, evicted oldest-first) and swept from _sweep_capture_tray, i.e. on every
# prepare.
# --------------------------------------------------------------------------- #

_CARRY_SHELF: dict = {}
_CARRY_SHELF_MAX = 24
_CARRY_SHELF_MAX_BYTES = 32 * 1024 * 1024
#: The tray's BYTE bound, and the reason it exists is that a count cap is not a
#: memory bound -- the same sentence _sweep_carry_shelf already carries about its
#: twin. Measured 2026-09-06 at the permitted ceilings with 1 MiB screens (real
#: device PNGs run 1-3 MiB): 240 MiB resident AFTER a full sweep, because the
#: tray had a count cap and a TTL and nothing else. Worse than an oversight --
#: revive_captures POPS items off the byte-bounded shelf and inserts them here,
#: so shelf-sized payloads migrated into the pool with no byte bound and freed
#: shelf capacity to be refilled. Held to the shelf's own figure so the two
#: pools share one budget rather than adding to each other.
#: Defined beside its twin on purpose: these two are one budget, and the
#: relation is asserted in tests/test_bounds_upper.py rather than described.
_CAPTURE_TRAY_MAX_BYTES = 32 * 1024 * 1024


def tray_max() -> int:
    """Per-call and resident screen cap of the capture tray (read at call time)."""
    return _CAPTURE_TRAY_MAX


def tray_max_bytes() -> int:
    """Byte budget of the capture tray (read at call time)."""
    return _CAPTURE_TRAY_MAX_BYTES


def shelf_max() -> int:
    """Screen cap of the carry-forward shelf (read at call time)."""
    return _CARRY_SHELF_MAX


def shelf_max_bytes() -> int:
    """Byte budget of the carry-forward shelf (read at call time)."""
    return _CARRY_SHELF_MAX_BYTES
