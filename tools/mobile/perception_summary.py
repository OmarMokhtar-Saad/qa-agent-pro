"""The ``fields`` and ``controls`` summary lines of the screen block."""

from __future__ import annotations

from tools.mobile.perception_limits import MAX_SUMMARY_LABEL_CHARS
from tools.mobile.perception_line import _eid, _rows


# ``id -> the label the summaries print``: abbreviated, never AMBIGUOUS.
#
# The ``fields`` and ``controls`` summaries exist because a model read three
# identical unlabelled Views and tapped Voice mode as Send. Nothing here may
# make two controls read the same, so the abbreviation is applied first and
# then any group whose SHORTENED labels collide while their FULL labels differ
# is restored to full length -- all of the group, not the shorter of it,
# because a reader comparing an abbreviation against a full string cannot tell
# which is which.
#
# Controls that genuinely share a label are left sharing it. That is the
# screen's own ambiguity, and hiding it would be the lie this pair of
# summaries was added to stop telling.
#
# KEYED THROUGH :func:`_eid`, which its two consumers also call. The first
# version wrote `str(id or "")` here and read `str(id)` there, so an element
# with `id` absent or `None` was stored under `""` and looked up under
# `"None"`: the lookup missed, the fallback was `""`, and the summary printed
# two controls as `None ` and `None ` -- or, with two empty ids, both as
# `Voice mode`. That is the incident these summaries exist to prevent,
# reintroduced by deriving one key in two places. Found by EXECUTING.
#
# KEYED BY OBJECT IDENTITY, not by the element's id string. `prune`
# assigns unique non-empty ids, but a STORED packet is an outside input
# and this module already ships a fixture whose first element has none.
# Keyed by id string, two such controls shared one entry and the last
# one won: both printed `Voice mode`, which is the incident the fields
# and controls summaries exist to prevent. Found by EXECUTING.
def _summary_labels(elements: object) -> dict:
    """``id -> the label the summaries print``: abbreviated, never AMBIGUOUS."""
    full, short = _short_labels(_rows(elements))
    return _disambiguate(full, short)


def _short_labels(rows: list) -> tuple[dict, dict]:
    """``(full, short)`` label per element, keyed by object identity."""
    full: dict = {}
    short: dict = {}
    for element in rows:
        key = id(element)
        label = str(element.get("label") or "")
        full[key] = label
        short[key] = (
            label[:MAX_SUMMARY_LABEL_CHARS] + "…"
            if len(label) > MAX_SUMMARY_LABEL_CHARS
            else label
        )
    return full, short


def _disambiguate(full: dict, short: dict) -> dict:
    """Restore full length to every group whose shortened labels collide."""
    groups: dict = {}
    for key, value in short.items():
        groups.setdefault(value, []).append(key)
    for keys in groups.values():
        if len(keys) > 1 and len({full[key] for key in keys}) > 1:
            for key in keys:
                short[key] = full[key]
    return short


def _fields_line(fields: list, labels: dict) -> str:
    """The ``fields`` summary: which elements take typing."""
    return "fields (type into these): " + "; ".join(
        _eid(element)
        + " "
        + (labels.get(id(element)) or str(element.get("label") or "") or "(unlabelled)")
        for element in fields[:10]
    )


def _controls_line(controls: list, labels: dict) -> str:
    """The ``controls`` summary: what can be tapped and what each is called."""
    return "controls (tap these): " + "; ".join(
        _eid(element)
        + " "
        + (labels.get(id(element)) or str(element.get("label") or ""))
        + (" [" + str(element.get("role")) + "]" if element.get("role") else "")
        for element in controls[:20]
    )


# The two summaries a chat screen needed and did not have: which element
# takes typing, and which controls can be tapped and what each is
# called. Without them the model read three identical unlabelled Views
# and tapped Voice mode as Send.
#
# Computed over ALL elements, not per summary: a field and a control
# whose abbreviations collide are as confusing as two controls, and the
# stricter input costs nothing.
def _summary_lines(elements: list[dict]) -> list[str]:
    """The ``fields`` and ``controls`` summary lines, each only when non-empty."""
    fields = [element for element in elements if element.get("editable")]
    controls = [
        element
        for element in elements
        if element.get("clickable") and element.get("label")
    ]
    labels = _summary_labels(elements)
    lines: list[str] = []
    if fields:
        lines.append(_fields_line(fields, labels))
    if controls:
        lines.append(_controls_line(controls, labels))
    return lines
