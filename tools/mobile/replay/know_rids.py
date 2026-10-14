"""Replay knowledge: the resource ids an action could hit."""

from __future__ import annotations

from typing import Any


def _norm(value: Any) -> str:
    """*value* with its whitespace collapsed, lower-cased."""
    return " ".join(str(value or "").split()).lower()


def _pick(target: Any, name: str) -> str:
    """The normalised *name* field of *target* (a dict or an object)."""
    raw = target.get(name) if isinstance(target, dict) else getattr(target, name, "")
    return _norm(raw)


def _element_hits(elements: list, words: list, want_id: str) -> list:
    """The rid of each element that *want_id* names or whose label holds a word."""
    hits: list = []
    for element in elements:
        if not isinstance(element, dict) or not element.get("rid"):
            continue
        labels = [_norm(element.get(f)) for f in ("text", "desc")]
        by_id = bool(want_id) and str(element.get("id") or "").lower() == want_id
        if by_id or any(w in label for w in words for label in labels if label):
            hits.append(str(element["rid"]))
    return hits


def _action_rids(action: Any, screen: Any) -> list:
    """Every resource id this action could hit: the target's own ``rid``, plus
    the rid of each element on *screen* that its ``id`` names or whose text or
    content-desc contains its ``text`` / ``label`` (``tap_text``'s ``text``
    too). So an avoid note on a rid also stops a tap aimed by its words."""
    target = getattr(action, "target", None)
    rids = [_pick(target, "rid")] if _pick(target, "rid") else []
    words = [w for w in (_pick(target, "text"), _pick(target, "label")) if w]
    own = _norm(getattr(action, "text", ""))
    if target is None and own:
        words.append(own)
    elements = screen.get("elements") if isinstance(screen, dict) else None
    return rids + _element_hits(elements or [], words, _pick(target, "id"))
