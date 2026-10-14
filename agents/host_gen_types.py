"""Host mode: the value types of the prepare pipeline. Moved verbatim from handlers/generate.py (C3c)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, NamedTuple, Optional

ProgressCb = Optional[Callable[[str], Awaitable[None]]]


@dataclass
class _Grounding:
    """Result of ground_and_gate when grounding + gating passed: the inputs
    _prepare_generation needs. Deliberately a SEPARATE helper rather than a
    refactor of handle_generate_test_cases, so applying this additive batch has
    zero server-path blast radius."""

    url_content: dict | None = None
    ui_content: dict | None = None
    openapi_text: str | None = None


@dataclass
class PreparePayloadResult:
    """Structured result of handle_prepare_test_cases.

    Exactly one of (payload + prep_id) or clarify is populated:
    * payload / prep_id: the grounded host-mode payload + its store handle.
    * clarify: a markdown string to relay verbatim (setup hint, preflight,
      clarifying questions, or an error) -- no suite was prepared.

    ``notice`` carries any NON-SILENT disclosure. It is written ONCE, on the
    success path (_host_mode_server_llm_notice); a further disclosure must be
    APPENDED, never assigned, or it will silently drop that one. The image-drop
    disclosure is emitted separately, as its own text block by
    assemble_prepare_payload. ``images`` (raw {data, mime} dicts) is
    populated in ops-3d-2 when the MCP tool result can carry image content.
    ops-3d-2/3d-3 render this into MCP content; render_prepare_payload turns it
    into plain text for a legacy (string-only) caller.
    """

    payload: dict | None = None
    prep_id: str = ""
    clarify: str = ""
    notice: str = ""
    images: list = field(default_factory=list)


@dataclass
class _PreflightCarry:
    """What the pre-flight resolved about re-sent and carried-forward screens."""

    capture_ids: list | None
    carried_ids: list
    carry_note: str
    carried_from: str = ""


class _ImageIntake(NamedTuple):
    """The attachments after empty-drop and capture merge, plus both counts."""

    attached_images: list | None
    attested: int
    captured: int


class _GateCtx(NamedTuple):
    """What both image-gate beats read: the source, the ticket and the images."""

    text: str
    jira_content_json: str
    attached_images: list | None
    attested: int
    captured: int


class _Beat1(NamedTuple):
    """Beat 1's outcome: the plan to proceed with, or the menu to return."""

    plan: str
    menu: str | None


class _TextOnlySkip(NamedTuple):
    """The text-only image-skip stamp: how many ticket screens, and their names."""

    total: int
    names: list


class _TicketNotes(NamedTuple):
    """The ticket recency and fidelity notes, computed before the envelope."""

    snapshot_updated: str
    stale: str
    recent: str
    content_chars: int
    condensed: str


class _ImageFlags(NamedTuple):
    """The host image-job stamps: job, relevance, preflight and require."""

    job: bool
    relevance: bool
    preflight: bool
    require: bool


class _PrepRun(NamedTuple):
    """Everything the envelope, payload, audit and reply of one prepare read."""

    text: str
    grounded: _Grounding
    prepared: Any
    capture_ids: list | None
    cap_labels: list
    image_carry_ack: bool
    carry: _PreflightCarry
    intake: _ImageIntake
    skip: _TextOnlySkip
    notes: _TicketNotes
    img: _ImageFlags
    host_amb: bool
    ac_job: bool
    checklist_job: bool
    rp_narrowed: bool
    prospective_images: list
    captured_shipped: int
