"""Request objects for the notes, knowledge and flows tool handlers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NotesRequest:
    """One ``qa_mobile_notes`` call: list or retire the notes of one app."""

    action: str = "list"
    package: str = ""
    run_id: str = ""
    note_id: int = 0
    reason: str = ""


@dataclass(frozen=True)
class KnowledgeRequest:
    """One ``qa_mobile_knowledge`` call: one app's saved knowledge."""

    action: str = "list"
    package: str = ""
    item_id: int | str = 0
    run_id: str = ""
    data: dict | None = None


@dataclass(frozen=True)
class FlowsRequest:
    """One ``qa_mobile_flows`` call: list, show or delete saved flows or routes."""

    action: str = "list"
    package: str = ""
    run_id: str = ""
    name: str = ""
    kind: str = "flow"
