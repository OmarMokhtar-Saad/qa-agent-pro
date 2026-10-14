"""Dump to element rows: parse, walk the nodes, keep the ones a tester can act on."""

from __future__ import annotations

from collections.abc import Iterator
from xml.etree import ElementTree

from tools.mobile.perception_bounds import _area, parse_bounds
from tools.mobile.perception_consts import (
    DOCTYPE_REFUSAL,
    EDITABLE_CLASS_HINTS,
    NOT_XML_REFUSAL,
    OVERSIZE_REFUSAL,
)
from tools.mobile.perception_guard import _clean
from tools.mobile.perception_limits import MAX_DUMP_BYTES
from tools.safe_parse import has_xml_decl, parse_xml

#: The row fields whose truthiness makes a node more than layout scaffolding.
_CONTENT_KEYS = ("text", "desc", "rid", "clickable", "scrollable", "editable")


def _flag(node: ElementTree.Element, name: str) -> bool:
    return str(node.get(name) or "").strip().lower() == "true"


def _short_class(full: str) -> str:
    tail = str(full or "").rsplit(".", 1)[-1]
    return tail[:60]


def _is_editable(full_class: str) -> bool:
    text = str(full_class or "")
    return any(hint in text for hint in EDITABLE_CLASS_HINTS)


def _parse_dump(xml: object) -> tuple[ElementTree.Element | None, str]:
    """``(root, "")`` for a usable dump, ``(None, refusal)`` for a refused one."""
    if not isinstance(xml, str) or not xml.strip():
        return None, NOT_XML_REFUSAL
    if len(xml.encode("utf-8", errors="replace")) > MAX_DUMP_BYTES:
        return None, OVERSIZE_REFUSAL
    if has_xml_decl(xml):
        return None, DOCTYPE_REFUSAL
    root = parse_xml(xml)
    if root is None:
        return None, NOT_XML_REFUSAL
    return root, ""


# Entirely off-screen: laid out but not visible to the tester.
def _off_screen(bounds: tuple, root_bounds: tuple) -> bool:
    return (
        bounds[0] >= root_bounds[2]
        or bounds[1] >= root_bounds[3]
        or bounds[2] <= root_bounds[0]
        or bounds[3] <= root_bounds[1]
    )


def _visible_nodes(
    nodes: list, frame: tuple | None
) -> Iterator[tuple[ElementTree.Element, tuple]]:
    """``(node, bounds)`` for every positive-area node that is not off-screen."""
    root_bounds = frame
    for node in nodes:
        bounds = parse_bounds(node.get("bounds"))
        if bounds is None or _area(bounds) <= 0:
            continue
        if root_bounds is None:
            root_bounds = bounds
        elif _off_screen(bounds, root_bounds):
            continue
        yield node, bounds


def _collect_elements(
    root: ElementTree.Element, frame: tuple | None
) -> tuple[list[dict], list[str], str, int]:
    """Walk the dump once: ``(elements, texts, first package, nodes considered)``."""
    nodes = list(root.iter("node"))
    elements: list[dict] = []
    texts: list[str] = []
    package = ""
    for node, bounds in _visible_nodes(nodes, frame):
        element = _node_element(node, bounds)
        if element is None:
            continue
        package = package or _clean(node.get("package"))
        if element["text"]:
            texts.append(element["text"])
        elements.append(element)
    return elements, texts, package, len(nodes)


def _node_flags(node: ElementTree.Element, full_class: str) -> dict:
    """The behavioural flags of a node, in the packet's key order."""
    return {
        "clickable": _flag(node, "clickable") or _flag(node, "long-clickable"),
        "editable": _is_editable(full_class),
        "secure": _flag(node, "password"),
        "checked": _flag(node, "checked"),
        "scrollable": _flag(node, "scrollable"),
    }


# Pure layout scaffolding: not a target, not assertable.
# A password input, from the dump's own attribute. It is
# the only signal that survives a field named in an
# alphabet this server cannot read, which is why the
# credential rule leans on the ELEMENT here and not on the
# action's chosen names alone.
# Kept per element only so the root-package fallback in
# `prune` has something to fall back TO; stripped before the
# packet is rendered, because it is the same on every element.
def _node_element(node: ElementTree.Element, bounds: tuple) -> dict | None:
    """One node as an element row, or ``None`` for pure layout scaffolding."""
    full_class = str(node.get("class") or "")
    hint = _clean(node.get("hint"))
    row = {
        **({"hint": hint} if hint else {}),
        "id": "",
        "cls": _short_class(full_class),
        "text": _clean(node.get("text")),
        "desc": _clean(node.get("content-desc")),
        "rid": _clean(node.get("resource-id")),
        "bounds": list(bounds),
        **_node_flags(node, full_class),
        "package": _clean(node.get("package")),
    }
    return row if any(row[key] for key in _CONTENT_KEYS) else None
