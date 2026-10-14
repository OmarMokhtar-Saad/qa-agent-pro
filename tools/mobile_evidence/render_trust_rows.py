"""Trust rows about provenance and joins: turn bounds, source, clock, windows."""

from __future__ import annotations

from tools.mobile_evidence import evidence
from tools.mobile_evidence.render_html import check, pill


def _turns_row(content: dict, integrity: dict) -> str:
    return check(
        pill("p-void", "turns"),
        "how a turn boundary was decided",
        "from the app's own turn-start marker, one app run at a time"
        if integrity.get("turnsFromNarration")
        else "from the turn id the app stamped on each event"
        if content["evidence"].report.get("turns")
        else "one turn per case: the profile has no turn-start marker",
    )


def _source_row(content: dict) -> str:
    return check(
        pill("p-void", "source"),
        "which capture this page read",
        "the app's own event log (events.ndjson), with the per-case logcat slices for the network lines"
        if content.get("source") == "ndjson"
        else "the per-case logcat slices only — " + str(content.get("reason") or ""),
    )


def _clock_row(content: dict) -> str:
    missing = content.get("clock_missing") or []
    return check(
        pill("p-ok" if not missing else "p-gap", "clock"),
        "device clock read at case start",
        "on every case"
        if not missing
        else "NOT on "
        + ", ".join(str(t) for t in missing[:6])
        + " — "
        + evidence.NO_CLOCK_NOTE,
    )


def add_provenance_rows(rows: list, content: dict, integrity: dict) -> None:
    """Append how turns were bounded, which capture was read, and the clock row."""
    rows.append(_turns_row(content, integrity))
    rows.append(_source_row(content))
    rows.append(_clock_row(content))


def _windows_row(clipped: list, inverted: list) -> str:
    return check(
        pill("p-gap", "windows"),
        "case windows clipped against a neighbour",
        "clipped: "
        + (", ".join(str(t) for t in clipped[:6]) or "none")
        + (
            " — clipped to a POINT (ran inside a neighbour): "
            + ", ".join(str(t) for t in inverted[:6])
            if inverted
            else ""
        ),
    )


def add_join_rows(rows: list, integrity: dict) -> None:
    """Append the join-note row and the clipped-windows row."""
    note = integrity.get("joinNote") or ""
    if note:
        rows.append(
            check(
                pill("p-void", "join"), "how events were attributed to cases", str(note)
            )
        )
    clipped = list(integrity.get("clipped") or [])
    inverted = list(integrity.get("inverted") or [])
    if clipped or inverted:
        rows.append(_windows_row(clipped, inverted))
