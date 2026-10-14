"""prep_status ports and the SubmitPorts bundle (C3a step 0).

The 13 ``handlers.prep_status`` names that tests patch stay behind these two
Protocols (plan 1b); collapse to direct calls is C6 work. ``SubmitPorts`` is
built per call by the handler, so nothing here captures a patched name.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Optional, Protocol

from agents.host_submit_ports_io import (
    AFn,
    DupSectionPort,
    Fn,
    ImageNotePort,
    IoPort,
    MediaDupPort,
    ReviewPort,
)
from agents.host_submit_ports_text import (
    CategoryPort,
    CommonPort,
    ElicitPort,
    OutPort,
    ReplyPort,
)

ProgressCb = Optional[Callable[[str], Awaitable[None]]]
AskCb = Optional[Callable[[str], Awaitable[object]]]


class PrepLoadPort(Protocol):
    """Load-side prep_status helpers: skew, sidecar and store-error notes."""

    version_skew_note: Fn
    code_skew_note: Fn
    load_open_prep: AFn
    superseded_sidecar_note: Fn
    merge_category_rows: Fn
    prep_store_error_reply: Fn
    fanout_incomplete_note: Fn
    merge_retained_sidecar: Fn


class PrepGatePort(Protocol):
    """Gate and trace prep_status helpers: retained sidecar, drops, volume, RTM."""

    with_retained_sidecar: Fn
    dropped_note: Fn
    image_relevance_gate: Fn
    volume_floor_note: Fn
    rtm_trace_detail: Fn
    rtm_orphan_note: Fn
    no_coverage_signal_note: Fn


@dataclass(frozen=True)
class SubmitPorts:
    """Everything the submit use case reaches outside itself, one field per port."""

    io: IoPort
    review: ReviewPort
    media: MediaDupPort
    image: ImageNotePort
    dup: DupSectionPort
    common: CommonPort
    elicit: ElicitPort
    reply: ReplyPort
    out: OutPort
    category: CategoryPort
    prep_load: PrepLoadPort
    prep_gate: PrepGatePort
