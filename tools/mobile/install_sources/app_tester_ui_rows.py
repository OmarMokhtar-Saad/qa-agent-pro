"""App Tester UI: the app list read as rows (display name + bundle id).

A list row is the ``:id/row`` node with ``:id/app_name`` and ``:id/bundle_id``
children. Only rows are candidates: release notes and other free text on an
app page never are. Ids are matched by suffix, so any App Tester package id
reads the same.
"""

from __future__ import annotations

import re
from typing import NamedTuple
from xml.etree import ElementTree

from tools.safe_parse import parse_xml

LIST_RID = ":id/appListRecyclerView"
ROW_RID = ":id/row"
LOADING_RID = ":id/progressListLoadingSpinner"
AAB_SETUP_RID = ":id/aab_instructions_dialog_container"
_END = "</hierarchy>"
_BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")
_SPLIT_RE = re.compile(r"[\s|.\-_]+")


class Row(NamedTuple):
    """One app in the App Tester list."""

    name: str
    bundle: str


def _rid(node: ElementTree.Element) -> str:
    return node.get("resource-id") or ""


def _child_text(row: ElementTree.Element, suffix: str) -> str:
    for node in row.iter("node"):
        if _rid(node).endswith(suffix):
            return (node.get("text") or "").strip()
    return ""


def _root(xml: str) -> ElementTree.Element | None:
    """The parsed dump; text after ``</hierarchy>`` (an adb trailer) is cut."""
    head, end, _ = (xml or "").rpartition(_END)
    return parse_xml(head + end if end else xml or "", refuse_decl=True)


def parse_rows(xml: str) -> list:
    """The list rows on this screen, top first; rows without a bundle id drop."""
    root = _root(xml)
    if root is None:
        return []
    rows = (
        Row(_child_text(node, ":id/app_name"), _child_text(node, ":id/bundle_id"))
        for node in root.iter("node")
        if _rid(node).endswith(ROW_RID)
    )
    return [row for row in rows if row.bundle]


def has_rid(xml: str, suffix: str) -> bool:
    root = _root(xml)
    return root is not None and any(_rid(n).endswith(suffix) for n in root.iter())


def is_loading(xml: str) -> bool:
    """True while the list screen shows its loading spinner."""
    return has_rid(xml, LOADING_RID)


def is_listing(xml: str) -> bool:
    """True when the screen is the App Tester app list (not an app page)."""
    return has_rid(xml, LIST_RID)


def _words(text: str) -> set:
    return {word for word in _SPLIT_RE.split(text.casefold()) if word}


_BUILD_WORDS = frozenset({"debug", "release", "staging", "dev", "beta", "alpha"})
MIN_DERIVED_WORDS = 2


def derive_label(package: str) -> str:
    """Words a row for *package* must hold, read from the package id itself.

    The top-level domain and build-type words drop out. Fewer than
    ``MIN_DERIVED_WORDS`` words is too loose to pick a row by: "" (ask).
    """
    parts = [seg for seg in str(package or "").split(".")[1:] if seg]
    words = [w for w in _words(" ".join(parts)) if w not in _BUILD_WORDS]
    return " ".join(sorted(words)) if len(words) >= MIN_DERIVED_WORDS else ""


def matching_rows(label: str, rows) -> list:
    """Rows whose name and bundle words hold every word of *label*."""
    want = _words(label)
    if not want:
        return []
    return [row for row in rows if want <= _words(row.name) | _words(row.bundle)]


def _top_edge(node: ElementTree.Element) -> int | None:
    found = _BOUNDS_RE.fullmatch(node.get("bounds") or "")
    return int(found.group(2)) if found else None


def list_at_top(xml: str) -> bool:
    """True when the list's first row starts at the list's own top edge: a
    list scrolled down has its first row cut off or lower. Without bounds on
    both, nothing is proven (False)."""
    root = _root(xml)
    if root is None:
        return False
    nodes = list(root.iter("node"))
    lists = [n for n in nodes if _rid(n).endswith(LIST_RID)]
    rows = [n for n in nodes if _rid(n).endswith(ROW_RID)]
    if not lists or not rows:
        return False
    top, first = _top_edge(lists[0]), _top_edge(rows[0])
    return top is not None and first is not None and first == top
