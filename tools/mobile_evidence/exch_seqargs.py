"""Parameter objects for the sequence primitives; field defaults equal the old keyword defaults."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SecMeta:
    """The count chip and the note of a pane section."""

    count: int | None = None
    note: str = ""


@dataclass(frozen=True)
class ToolParts:
    """What follows the name on a tool row."""

    args: str = ""
    note: str = ""
    body: str = ""


@dataclass(frozen=True)
class FlowParts:
    """What follows the head on a flow row."""

    bits: str = ""
    body: str = ""
    pill_html: str = ""


@dataclass(frozen=True)
class CapParts:
    """What follows the id on a capability row."""

    text: str = ""
    prefill: str = ""
    started: bool = True
    pill_html: str = ""


@dataclass(frozen=True)
class LogParts:
    """What follows the head on a narrated log row."""

    body: str = ""
    bad: bool = False
    times: int = 1
    pill_html: str = ""


@dataclass(frozen=True)
class SeqRow:
    """One sequence row: its kind, clock, inner html and optional waterfall bar."""

    kind: str
    clock: str
    inner: str
    bar: str = ""


@dataclass(frozen=True)
class SessionParts:
    """What follows the note on the session opener."""

    pill_html: str = ""
    extra: str = ""
    label: str | None = "session"


@dataclass(frozen=True)
class PromptNote:
    """The note, head and meta chips of a prompt row."""

    note: str = ""
    head: str = ""
    meta: str = ""
