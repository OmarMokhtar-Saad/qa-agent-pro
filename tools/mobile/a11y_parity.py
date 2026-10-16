"""The parity check: the fast dump must describe the same nodes as uiautomator."""

from __future__ import annotations

import hashlib
from typing import Any

from tools.mobile import perception

#: Share of nodes (by class, id, bounds) both dumps must have in common.
A11Y_PARITY_OVERLAP_FRACTION = 0.9


def _node_key(element: dict) -> tuple:
    return (
        str(element.get("cls") or ""),
        str(element.get("rid") or ""),
        tuple(element.get("bounds") or []),
    )


def _node_value(element: dict) -> tuple:
    return (
        str(element.get("text") or ""),
        str(element.get("desc") or ""),
        bool(element.get("clickable")),
        bool(element.get("editable")),
        bool(element.get("secure")),
    )


def _keyed(xml: str):
    # Both dumps are of the same screen, so they share whatever frame prune derives.
    pruned = perception.prune(xml, "", perception.ScreenView())
    if not isinstance(pruned, dict) or pruned.get("error"):
        return None
    body: Any = (
        pruned.get("content") if isinstance(pruned.get("content"), dict) else pruned
    )
    out: dict = {}
    for element in body.get("elements") or []:
        if not isinstance(element, dict):
            continue
        out[_node_key(element)] = _node_value(element)
    return out


def node_digest(xml: str) -> str:
    """A digest of the nodes parity compares, or ``""`` when *xml* cannot be pruned."""
    keyed = _keyed(xml)
    if keyed is None:
        return ""
    return hashlib.sha1(repr(sorted(keyed.items())).encode("utf-8")).hexdigest()


def _unparsed() -> dict:
    return {
        "error": "a11y parity check: a dump could not be parsed",
        "content": None,
    }


def _overlap_error(fast: dict, slow: dict) -> dict | None:
    union = set(fast) | set(slow)
    shared = set(fast) & set(slow)
    if not union or len(shared) / len(union) < A11Y_PARITY_OVERLAP_FRACTION:
        return {
            "error": "a11y parity check: the two dumps share too few nodes",
            "content": None,
        }
    return None


def compare(fast_xml: str, slow_xml: str) -> dict:
    """Refuse the fast dump unless it describes the same nodes as uiautomator."""
    fast, slow = _keyed(fast_xml), _keyed(slow_xml)
    if fast is None or slow is None:
        return _unparsed()
    refused = _overlap_error(fast, slow)
    if refused:
        return refused
    shared = set(fast) & set(slow)
    differing = sum(1 for key in shared if fast[key] != slow[key])
    # Zero tolerance, not a cap: the destructive guard must see the same node either way.
    if differing:
        return {
            "error": "a11y parity check: "
            + str(differing)
            + " node(s) differ from uiautomator",
            "content": None,
        }
    return {"error": None, "content": {"shared": len(shared)}}
