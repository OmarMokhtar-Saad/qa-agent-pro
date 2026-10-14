"""The sentence and bullet lines every surface prints for a screen audit."""

from __future__ import annotations

from tools.mobile import perception_text
from tools.mobile.saud_consts import CLEAN_NOTE, KIND_LABELS, NO_NODES_NOTE


def _dropped_count(content: dict) -> int:
    """THE DROP COUNTS ARE PRUNE'S, not a second derivation of "what was lost".

    `truncated` keeps its own meaning there (the dump offered more than
    MAX_ELEMENTS) and is reported under that name.
    """
    return (
        perception_text._count(content.get("dropped_inside_panel"))
        + perception_text._count(content.get("dropped_outside_panel"))
        + perception_text._count(content.get("dropped_unclassified"))
    )


def _head_line(findings: list, audited: int) -> str:
    if not findings:
        return CLEAN_NOTE
    return (
        str(len(findings))
        + " accessibility finding"
        + ("" if len(findings) == 1 else "s")
        + " over "
        + str(audited)
        + " element"
        + ("" if audited == 1 else "s")
        + "."
    )


def _partial_tail(result: dict, audited: int, dropped: int) -> str:
    return (
        " THIS AUDIT IS PARTIAL: "
        + str(dropped)
        + " element(s) this screen offered were dropped before it ran"
        + (
            " and the screen exceeded the element cap"
            if result.get("truncated")
            else ""
        )
        + ", so it describes "
        + str(audited)
        + " of a larger screen and its silence is not evidence."
    )


# THE sentence every surface prints, composed once.
#
# Partiality is part of the sentence rather than a caveat a caller may forget:
# a count of findings over a truncated element set describes some of a screen,
# and saying so is not optional.
def _note(result: dict) -> str:
    """The audit note: head line, cap line, partial tail."""
    findings = list(result.get("findings") or [])
    capped = int(result.get("capped") or 0)
    audited = int(result.get("audited") or 0)
    dropped = int(result.get("dropped") or 0)
    if not result.get("auditable"):
        return NO_NODES_NOTE
    head = _head_line(findings, audited)
    if capped:
        head += " " + str(capped) + " further finding(s) were not carried."
    if dropped or result.get("truncated"):
        head += _partial_tail(result, audited, dropped)
    return head


def _finding_line(finding: dict) -> str:
    kind = str(finding.get("kind") or "")
    ids = ",".join(str(value) for value in (finding.get("ids") or []))
    name = str(finding.get("name") or "") and ('"' + str(finding["name"]) + '" ')
    return (
        "- "
        + KIND_LABELS.get(kind, kind)
        + " ["
        + ids
        + "] "
        + name
        + str(finding.get("detail") or "")
    )
