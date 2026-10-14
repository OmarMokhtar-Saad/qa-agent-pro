"""Walk the elements of one screen and collect the audit findings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tools.mobile.saud_consts import (
    KIND_AMBIGUOUS_NAME,
    KIND_SMALL_TARGET,
    MAX_FINDINGS,
    NO_NODES_NOTE,
)
from tools.mobile.saud_density import (
    _apply_density,
    _empty,
    _small_side,
    _target_detail,
)
from tools.mobile.saud_names import (
    _finding,
    _is_interactive,
    _naming_findings,
    _rid_name,
    _Said,
    _Who,
)
from tools.mobile.saud_note import _dropped_count, _note


@dataclass
class _Ctx:
    """One screen's running state: the floor, the result, findings and names so far."""

    floor: int
    result: dict
    findings: list
    by_name: dict


def _small_target_finding(element: dict, who: _Who, ctx: _Ctx) -> None:
    side = _small_side(element, ctx.floor)
    if side:
        detail = _target_detail(side, ctx.floor, ctx.result)
        said = _Said([who.eid], who.label or who.rid_name, detail)
        ctx.findings.append(_finding(KIND_SMALL_TARGET, said))


def _audit_element(element: dict, ctx: _Ctx) -> None:
    """Append this element's findings and record its spoken name in ``by_name``."""
    eid = str(element.get("id") or "?")
    label = str(element.get("label") or "").strip()
    who = _Who(eid, label, _rid_name(element.get("rid")))
    _naming_findings(element, who, ctx.findings)
    if not _is_interactive(element):
        # A decorative element without a label is CORRECT. Flagging it is
        # what makes an audit ignorable.
        return
    _small_target_finding(element, who, ctx)
    spoken = label or who.rid_name
    if spoken:
        key = (" ".join(spoken.lower().split()), str(element.get("role") or ""))
        ctx.by_name.setdefault(key, []).append(eid)


def _ambiguous_findings(by_name: dict) -> list:
    """ONE finding per NAME, never per element.

    A list of ten "Delete" rows is one ambiguity, and ten rows of it would
    spend the whole cap on a single defect.
    """
    return [
        _finding(
            KIND_AMBIGUOUS_NAME,
            _Said(
                ids,
                spoken,
                str(len(ids)) + " interactive controls answer to this one name, so a "
                "user navigating by name cannot tell them apart",
            ),
        )
        for (spoken, _role), ids in by_name.items()
        if len(ids) > 1
    ]


def _collect_findings(elements: list, floor: int, result: dict) -> list:
    """Per-element findings first, then the duplicate-name findings."""
    ctx = _Ctx(floor, result, [], {})
    for element in elements:
        _audit_element(element, ctx)
    ctx.findings.extend(_ambiguous_findings(ctx.by_name))
    return ctx.findings


def _measure(content: dict, density_dpi: Any, elements: list) -> dict:
    result = _empty(True)
    # ONE coercion of this value, inside the guard that admits it may not be
    # representable, and everything derived from it computed there too. The
    # reader already rejects an implausible dpi, but by the time it arrives
    # here it is a stored packet's worth of trust, and it is a MULTIPLIER on
    # the floor -- a bad one rescales the check rather than failing it, so
    # out of range means "the device did not answer". Derived values land in
    # locals and are assigned together: a raise between two assignments
    # would otherwise leave a result claiming a measured density while still
    # carrying the default floor.
    _apply_density(result, density_dpi)
    result["dropped"] = _dropped_count(content)
    result["truncated"] = bool(content.get("truncated"))
    result["audited"] = len(elements)
    return result


def _audit_result(content: dict, density_dpi: Any) -> dict:
    elements = [e for e in (content.get("elements") or []) if isinstance(e, dict)]
    result = _measure(content, density_dpi, elements)
    if not elements:
        result["note"] = NO_NODES_NOTE
        return result
    result["auditable"] = True
    result["complete"] = not result["dropped"] and not result["truncated"]
    findings = _collect_findings(elements, result["min_target_px"], result)
    result["capped"] = max(0, len(findings) - MAX_FINDINGS)
    result["findings"] = findings[:MAX_FINDINGS]
    result["note"] = _note(result)
    return result
