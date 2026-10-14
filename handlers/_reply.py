"""Moved from handlers (refactor P2): _reply.

The reply shapers live in ``handlers.reply_*``; this module re-exports them (the
patch surface the tests and the agents ports read) and keeps `settings`, the
logger and the device-probe wrapper, whose budget is read at call time.
"""

from __future__ import annotations

import logging

from agents.host_echo import _UNREC_SHOWN, _unrecognized_names_line  # noqa: F401
from agents.host_echo import clip_echo as _clip_echo  # noqa: F401
from handlers.reply_budget import assemble_finalize_reply, summary_budget  # noqa: F401
from handlers.reply_consts import (  # noqa: F401
    _REPLY_CAP,
    _REPLY_P_EXPORTED,
    _REPLY_P_POINTER,
    _REPLY_P_PROVENANCE,
    _REPLY_P_REPORT,
    _SUMMARY_CAP,
    _SUMMARY_FLOOR,
    _TRUNC_RESERVE,
)
from handlers.reply_device_probe import normalize_rows, probe_rows
from handlers.reply_devices import DeviceListing, shape_devices  # noqa: F401
from handlers.reply_generation import (  # noqa: F401
    _GENERATION_STATUS_TEXT,
    ExportedFile,
    GenerationReply,
    _truncate_summary,
    shape_explore_step,
    shape_export_result,
    shape_generation_result,
)
from handlers.reply_section import (  # noqa: F401
    ReplySection,
    _omission_marker,
    _sections_to_drop,
    _unbounded_reply,
)
from handlers.reply_suite import (  # noqa: F401
    _NO_RECOVERY_DECLARED,
    _staged_names_suffix,
    _submission_bytes,
    _suite_unreadable_reply,
    oversized_full_suite_note,
)

logger = logging.getLogger("tools.mcp_handlers")

#: How long qa-doctor's ONE device probe may take. Small, because this report is
#: what a tester runs when nothing works.
MOBILE_PROBE_S = 8.0


# Design notes for `android_device_details` (kept as a comment so the function
# itself stays within the 50-line limit):
#
# A tester asked which emulator was running and could not be told: the row
# said `sdk gphone64 arm64` and nothing else -- no AVD name, no API level
# (2026-09-08).
#
# NO NEW PRODUCER. ``tools.mobile.emulator.avd_name_of`` stays the one
# producer of an AVD name and ``tools.mobile.adb.device_facts`` the one
# producer of the API level; nothing is re-derived here, because two
# derivations of one fact drift.
#
# THE STATED ELSE: a build with no ``tools/mobile`` on disk (the
# test-cases-only edition -- ``mobile_modules_present`` is this file's single
# predicate for that) gets its rows EXACTLY as before, rather than a second,
# duplicate `adb` probe written here. A probe that fails, times out or finds
# nothing also leaves its row untouched: ``device_manager`` never raises, and
# this must not be the thing that changes that. Each probe is bounded by
# ``MOBILE_PROBE_S`` for the reason ``_mobile_doctor_section`` states -- a
# device probe that hangs turns a listing into the symptom.
#
# Returns COPIES, so no caller's dicts are mutated under it, and the added
# keys are purely additive for every other reader of a device row.
#
# ONE ROW OUT PER ROW IN. A row this server cannot read used to be DROPPED,
# and `handle_list_devices` audits `len(devices)` -- so the count followed the
# drop and the listing under-reported what `device_manager` produced, with no
# trace. It is normalised instead, and rendered as unreadable.
#
# THE BUDGET GATES STARTING WORK, NEVER THE WORK ALREADY STARTED. Each probe
# was bounded and the LISTING was not: five hanging devices cost five budgets
# inside a single MCP call (40s measured). A device that is ADMITTED keeps
# both of its own full `MOBILE_PROBE_S` probes, so a single slow-but-working
# device answers exactly as it does today -- sharing one budget ACROSS a
# row's two probes would time the second one out where it succeeds now, which
# is a regression this bound is not worth. What is bounded is the listing:
# once the budget is spent no further device is probed, so the worst case is
# one budget plus ONE ROW'S TWO PROBES whatever the device count -- three
# times ``MOBILE_PROBE_S``, because the gate is checked BEFORE a row and a
# row admitted just inside the budget still runs both of its own probes. This
# said "one budget plus one probe", i.e. 16s; measured, it is 23.9s. A
# documented bound that measurement contradicts is a false claim, and a
# reader sizing a client timeout off it would size it short. A device reached
# after that keeps its row exactly as it arrived -- the same degradation a
# failed or timed-out probe already had.
async def android_device_details(devices: list) -> list:
    """*devices* with ``avd`` / ``api`` filled in for Android rows. Never raises.

    Rationale and the probe-budget bound: the comment block above."""
    return await probe_rows(normalize_rows(devices), MOBILE_PROBE_S)
