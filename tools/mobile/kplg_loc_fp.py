"""Element fingerprints and the similarity heal match."""

from __future__ import annotations

from typing import Any, cast

from tools.mobile import knowledge_limits, screen_key
from tools.mobile.kplg_loc_str import locator_of

#: A heal's best score must beat the runner-up by this much.
HEAL_MARGIN = 0.1
_WEIGHTS = {
    "text_shape": 0.3,
    "desc": 0.2,
    "cls": 0.2,
    "anc_rids": 0.15,
    "pos_bucket": 0.15,
}
_EVIDENCE_NEEDED = 0.5


def _box(node: dict) -> tuple | None:
    raw = node.get("bounds")
    if isinstance(raw, (list, tuple)) and len(raw) == 4:
        try:
            return tuple(int(v) for v in raw)
        except (TypeError, ValueError, OverflowError):
            return None
    return None


def _area(box: tuple) -> int:
    return max(0, box[2] - box[0]) * max(0, box[3] - box[1])


def ancestors_of(node: dict, nodes: list) -> list:
    """Nodes whose box strictly contains *node*'s, innermost first."""
    box = _box(node)
    if not box:
        return []
    found = []
    for other in nodes:
        outer = _box(other) if other is not node else None
        if outer and outer != box and outer[0] <= box[0] and outer[1] <= box[1]:
            if outer[2] >= box[2] and outer[3] >= box[3]:
                found.append((_area(outer), other))
    found.sort(key=lambda pair: pair[0])
    return [other for _, other in found]


def _nodes(screen: Any) -> list:
    body = screen if isinstance(screen, dict) else {}
    if isinstance(body.get("content"), dict):
        body = body["content"]
    raw = body.get("elements")
    return [e for e in raw if isinstance(e, dict)] if isinstance(raw, list) else []


def fp_of(node: dict, nodes: list) -> dict:
    return screen_key.element_fp(node, ancestors_of(node, nodes))


def _same(a: Any, b: Any) -> float:
    return 1.0 if a == b else 0.0


def _jaccard(a: list, b: list) -> float:
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb) if sa | sb else 1.0


def score(fp: dict, other: dict) -> float:
    """Similarity of two fingerprints over text_shape/desc/cls/anc_rids/pos.
    Fields empty on both sides carry no evidence; too little evidence => 0."""
    total = used = 0.0
    for name, weight in _WEIGHTS.items():
        a, b = (
            fp.get(name) or ("" if name != "anc_rids" else []),
            other.get(name) or ("" if name != "anc_rids" else []),
        )
        if not a and not b:
            continue
        used += weight
        total += weight * (
            _jaccard(cast("list", a), cast("list", b))
            if name == "anc_rids"
            else _same(a, b)
        )
    return total / used if used >= _EVIDENCE_NEEDED else 0.0


def best_match(fp: dict, nodes: list) -> dict | None:
    """The one node that clearly matches *fp*, else ``None`` (ambiguity refuses)."""
    cls = str(fp.get("cls") or "")
    pool = [n for n in nodes if not cls or str(n.get("cls") or "") == cls]
    scored = []
    for node in pool[: knowledge_limits.HEAL_MAX_CANDIDATES]:
        scored.append((score(fp, fp_of(node, nodes)), node))
    scored.sort(key=lambda pair: -pair[0])
    if not scored or scored[0][0] < knowledge_limits.HEAL_MIN_SCORE:
        return None
    if len(scored) > 1 and scored[0][0] - scored[1][0] < HEAL_MARGIN:
        return None
    return scored[0][1]


def _heal_locator(node: dict) -> str:
    for kind, field in (("rid", "rid"), ("text", "text"), ("text", "desc")):
        if node.get(field):
            return locator_of(kind, node[field])
    return ""
