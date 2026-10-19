"""Every constant of the per-run store, in one home.

Scalar bounds, file names, key sets and id regexes live here and nowhere else;
the ``run_*`` siblings read them as ``run_consts.NAME`` so a patch on this
module bites every reader.
"""

from __future__ import annotations

import re

from tools.mobile import paths

#: What a secret value is replaced with, everywhere.
SECRET_MASK = "***"

#: Keys whose value is masked when the containing object is marked secret.
#: Masked wherever they appear, with or without a ``secret`` marker, because a
#: key with one of these names holds a credential by definition. This is the
#: defence-in-depth half of :func:`redact`: the marker-based half below is the
#: contract, and this is what survives a caller forgetting it.
ALWAYS_MASKED_KEYS: frozenset[str] = frozenset(
    {"password", "passcode", "pin", "otp", "secret_value", "credential"}
)

SECRET_VALUE_KEYS: tuple[str, ...] = (
    "text",
    "value",
    "tester_input",
    "input",
    "password",
    "secret_value",
)

#: The ``runs/`` folder prefix of a pre-run owner's records
#: (see ``locks_state.record_segment``); run listings hide these folders.
PRERUN_DIR_PREFIX = "prerun-"

#: A lease whose heartbeat is older than this is up for grabs.
LEASE_STALE_S = 120
#: How long a cached (package, serial) resolution for one task/app is trusted
#: before a rerun re-discovers it from the device. Long enough that a
#: same-session retry of a failed run skips the fuzzy-match and device-probe
#: round trips a repeated goal used to pay for on every rerun; short enough
#: that a device wipe or app reinstall days later is not trusted blindly --
#: the reader still probes the device before ACTING on a hit, this only skips
#: the DISCOVERY round trips, never the safety checks.
RESOLVED_APP_TTL_S = 7 * 24 * 3600
#: Entries in the cross-run resolved-app cache. Bounded so a long-lived
#: machine cannot grow this file without limit; oldest `cached_at` entries
#: are evicted first.
MAX_RESOLVED_APPS = 50
RESOLVED_APPS_FILE = "resolved_apps.json"

#: A run whose newest activity is older than this is collected when the run list
#: is read. Seven days: long enough that a tester who was away for a week still
#: finds their results, short enough that a machine does not accumulate run
#: directories forever.
STALE_RUN_S = 7 * 24 * 3600

LEASE_FILE = "lease.json"
MANIFEST_FILE = "manifest.json"
#: Which chat already holds this run's packet static block. See
#: :func:`briefed_session`. NOT kept in the lease, which is rewritten wholesale
#: on every heartbeat -- surviving that is the marker's whole job.
BRIEFING_FILE = "briefing.json"
SCREEN_MEMO_FILE = "screen_memo.json"
CASES_DIR = "cases"
SCREENS_DIR = "screens"
OBSERVATIONS_DIR = "observations"
#: One PNG per OBSERVATION -- the same key ``observations/`` uses, minted by the
#: same :func:`observation_key`, because a picture is a look at a screen and not
#: a screen. Keying these on the bare ``screen_id`` would store ONE frame for a
#: four-turn conversation and silently overwrite three, which is the defect
#: a0217ab9 fixed for the wireframes.
#:
#: A SIBLING of both libraries under the same run directory -- which is what
#: makes deletion free: ``gc_stale_runs`` rmtree's the whole run, so a frame can
#: never outlive the run it describes.
SHOTS_DIR = "shots"

#: How many DISTINCT observations one run may keep. An observation is a pruned
#: screen, so ``perception.MAX_ELEMENTS`` and ``perception.MAX_ATTR_CHARS``
#: bound one at roughly 45 KB worst case; this is the multiplier that turns that
#: into a directory on the tester's own disk, kept until ``STALE_RUN_S``
#: collects it. Reaching it stops storing NEW observations and nothing else: the
#: canonical screen is still written, the run is untouched, and the report says
#: the limit was reached rather than showing a stale picture as if it were the
#: step's own.
MAX_RUN_OBSERVATIONS = 400
#: Stored screen PNGs kept for ONE run.
#:
#: A SECOND cap and not a reuse of the one above, because the two bound
#: different axes: an observation is ~4 KB of JSON and a resized PNG is two
#: orders of magnitude larger, so one number cannot state both constraints.
#: Held at the same 400 so the two libraries stay in step -- a frame whose
#: observation was dropped has nothing to attach to.
#:
#: Reaching it stops storing NEW frames and nothing else: the observation and
#: the canonical screen are already written, and the report says per screen that
#: no picture was stored rather than showing another screen's.
#:
#: Counted over the DIRECTORY, exactly as ``_write_observation`` counts: a run
#: resumed from another chat has no in-memory count, and a cap that resets on
#: resume is not a cap.
MAX_RUN_SHOTS = 400

_RUN_ID_RE = paths.RUN_ID_RE
_TC_ID_RE = re.compile(r"^TC-\d{3,6}$")
_SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

#: Lease states a caller branches on.
HELD = "held"
TAKEN_OVER = "taken_over"
NONE = "none"


#: The SHAPE :func:`session.mint_run_id` produces: ``mrun-YYYYmmdd-HHMMSS-hex6``.
#: Deliberately separate from :func:`valid_run_id`, which answers a DIFFERENT
#: question -- "is this safe to use as a path segment" -- and therefore accepts
#: every single-token status string in this lane (`handoff_failed`, `not_held`,
#: `already_held`). Using the safety check as an identity check put
#: `run_id="handoff_failed"` one key name away from a tester's screen, and it is
#: the same conflation that made a bare `run_id` a bad lock-owner label.
_RUN_ID_SHAPE_RE = re.compile(r"^mrun-\d{8}-\d{6}-[0-9a-f]{6}$")

EVIDENCE_DIR = "evidence"

#: A file name inside ``evidence/``: one path segment, bounded charset.
_EVIDENCE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}$")
