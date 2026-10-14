"""Join what the LANE did to what the APP did, for one mobile run -- keyed on the CASE.

A run is witnessed twice and neither witness sees the whole thing. The lane's checkpoints
know the case: which tc_id, when it started and ended (host clock), every replayed action
with its ``at`` stamp. The app's own log knows the model round-trips, every endpoint call,
every tool call, the flow-engine state and the cards it pushed -- and nothing about cases.
This module is the seam.

THE JOIN IS BY TIME, AND IT IS HONEST ABOUT ITS CLOCKS. The lane types nothing the app
narrates, so the reference's text match is unavailable; the only shared axis is time.

  * A case's WINDOW is ``[started, updated]`` on the host clock, moved onto the device
    clock by the case's ``clock_offset_ms`` (device = host + offset), which the lane
    measured at case start.
  * When the offset is ``None`` (the device clock could not be read) the window is
    widened by ``TOLERANCE_MS`` on each side and then CLIPPED at the midpoint to each
    neighbouring case's window -- neighbours being adjacent by ``started`` -- so two
    windows can never overlap. The report says "device clock not read".
  * An app turn belongs to the ONE case whose window its ``[firstTs, lastTs]`` overlaps.
    A turn that overlaps no window but lies between two cases is ``ambiguous`` -- "could
    belong to an adjacent case" -- and lands on NEITHER card. A turn before the first or
    after the last case is ``outside``. Nothing is guessed, nothing is dropped in silence.
  * The turn text is used only as CONFIRMATION when the case's trace carries a typed
    literal -- it never does on a file this tree wrote, so the usual verdict is "not
    confirmed", which is not a mismatch.
  * A profile with no turn-start marker yields no turns at all; then every record whose
    clock falls inside a case's window is attributed to ONE synthetic turn per case, and
    the join says so.

Three-state ``ok`` (True / False / None) is preserved end to end: an unanswered request is
None, never False. Nothing here raises to a caller: :func:`build` returns
``{"error", "content"}``.

This module is a facade: the code lives in the ``evidence_*`` siblings. Its scalar
constants (``TOLERANCE_MS`` ...) are copies bound at import; their one home is
``evidence_notes``, so patch them there.
"""

from __future__ import annotations

import calendar
import datetime
import json
from typing import NamedTuple

from tools.mobile_evidence import grammar
from tools.mobile_evidence.evidence_build import (
    BuildInputs,
    _latest_run_end,
    _resolve_report,
    build,
    empty_report,
)
from tools.mobile_evidence.evidence_clock import (
    _epoch_ms,
    _resolve_year,
    capture_offset,
    normalise_clock,
    slice_window,
    stamp,
)
from tools.mobile_evidence.evidence_join import (
    _join_from_placed,
    _join_note_bits,
    join,
)
from tools.mobile_evidence.evidence_join_model import (
    Join,
    JoinMeta,
    Tallies,
)
from tools.mobile_evidence.evidence_notes import (
    AMBIGUOUS_NOTE,
    DERIVED_NOTE,
    INVERTED_NOTE,
    NO_CLOCK_NOTE,
    NO_TURN_IN_ANY_CASE_NOTE,
    ONE_TURN_NOTE,
    SCHEMA,
    STREAMS,
    TOLERANCE_MS,
)
from tools.mobile_evidence.evidence_place import (
    _place_turn,
    _typed_texts,
)
from tools.mobile_evidence.evidence_run import (
    Evidence,
    EvidenceFrame,
)
from tools.mobile_evidence.evidence_slots import (
    _file_outside_turn,
    _net_log_call,
    _session_slot,
)
from tools.mobile_evidence.evidence_timing import (
    _collect_record_spans,
    _collect_turn_spans,
    _derived_ms,
    _first_clock_by_turn,
    _stamped,
    _ts_of,
)
from tools.mobile_evidence.evidence_totals import (
    _LlmSpend,
)
from tools.mobile_evidence.evidence_windows import (
    _clamp_inverted,
    _clip_to_neighbours,
    _ms,
    _window_row,
    case_windows,
    fence,
)

__all__ = [
    "AMBIGUOUS_NOTE",
    "BuildInputs",
    "DERIVED_NOTE",
    "Evidence",
    "EvidenceFrame",
    "INVERTED_NOTE",
    "Join",
    "JoinMeta",
    "NO_CLOCK_NOTE",
    "NO_TURN_IN_ANY_CASE_NOTE",
    "NamedTuple",
    "ONE_TURN_NOTE",
    "SCHEMA",
    "STREAMS",
    "TOLERANCE_MS",
    "Tallies",
    "_LlmSpend",
    "_clamp_inverted",
    "_clip_to_neighbours",
    "_collect_record_spans",
    "_collect_turn_spans",
    "_derived_ms",
    "_epoch_ms",
    "_file_outside_turn",
    "_first_clock_by_turn",
    "_join_from_placed",
    "_join_note_bits",
    "_latest_run_end",
    "_ms",
    "_net_log_call",
    "_place_turn",
    "_resolve_report",
    "_resolve_year",
    "_session_slot",
    "_stamped",
    "_ts_of",
    "_typed_texts",
    "_window_row",
    "build",
    "calendar",
    "capture_offset",
    "case_windows",
    "datetime",
    "empty_report",
    "fence",
    "grammar",
    "join",
    "json",
    "normalise_clock",
    "slice_window",
    "stamp",
]
