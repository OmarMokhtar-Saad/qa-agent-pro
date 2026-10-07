"""Executor plug locators: the learned locator queue and the similarity heal.

Locators are strings ``"rid:<v>"``, ``"label:<v>"`` or ``"text:<v>"``. A learned
element row (snapshot ``"elements"``) holds its fingerprint and a ranked queue.

``resolve_target`` acts only when the default resolution MISSED a targeted
action: it tries the queue in order (first node found replaces the target), then
a similarity heal against the stored fingerprint. A heal needs a score >=
``HEAL_MIN_SCORE`` and a unique best (margin ``HEAL_MARGIN``); an ambiguous one
never taps. Nothing here touches the DB; what was tried and used goes into
``ctx.kbuf`` and ``post_step`` copies it onto the step entry (``locator_used``,
``locator_tried``) for the learning stage. The destructive guard still judges
whatever node comes back.
"""

from __future__ import annotations

import json
import logging

from tools.mobile import knowledge_db, knowledge_hooks, knowledge_limits, screen_key

logger = logging.getLogger(__name__)

PRIORITY = 40

#: A heal's best score must beat the runner-up by this much.
HEAL_MARGIN = 0.1
HEAL_PREFIX = "heal:"
FP_PREFIX = "fp:"
PENDING = "locator_pending"
_KINDS = ("rid", "label", "text")
_WEIGHTS = {
    "text_shape": 0.3,
    "desc": 0.2,
    "cls": 0.2,
    "anc_rids": 0.15,
    "pos_bucket": 0.15,
}
_EVIDENCE_NEEDED = 0.5
_SKIP_OPS = frozenset({"assert", "scroll", "wait", "done", "ask_tester"})
_LOCATOR_CLIP = 150


def locator_of(kind: str, value: object) -> str:
    return "%s:%s" % (kind, str(value or "")[:_LOCATOR_CLIP])


def split_locator(loc: object) -> tuple:
    kind, _, value = str(loc or "").partition(":")
    return (kind, value) if kind in _KINDS and value else ("", "")


def own_locators(step: object) -> list:
    """The step's own selectors as locator strings, rid first."""
    target = (
        step.get("target") if isinstance(step, dict) else getattr(step, "target", None)
    )
    if target is None:
        return []
    out = []
    for kind in _KINDS:
        got = (
            target.get(kind) if isinstance(target, dict) else getattr(target, kind, "")
        )
        if got:
            out.append(locator_of(kind, got))
    return out


def parse_queue(raw: object) -> list:
    """``[{"l": locator, "h": hits, "m": misses}]`` from ``locators_json``."""
    try:
        got = json.loads(str(raw or "[]"))
    except (ValueError, TypeError):
        return []
    out = []
    for item in got if isinstance(got, list) else []:
        if isinstance(item, dict) and split_locator(item.get("l"))[0]:
            out.append(
                {
                    "l": item["l"],
                    "h": int(item.get("h") or 0),
                    "m": int(item.get("m") or 0),
                }
            )
    return out


def fp_token(fp: dict) -> str:
    return FP_PREFIX + json.dumps(fp, sort_keys=True, separators=(",", ":"))


def snapshot(conn, facts) -> dict:
    """``{"elements": {screen_key: [row]}}`` of the active element rows."""
    out: dict = {}
    rows = knowledge_db.rows(
        conn,
        "elements",
        "status = 'active' AND invalid_at IS NULL",
        (),
        knowledge_limits.SNAPSHOT_MAX_ROWS,
    )
    for row in sorted(rows, key=lambda r: r["id"]):
        out.setdefault(row["screen_key"], []).append(row)
    return {"elements": out}


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


def _nodes(screen: object) -> list:
    body = screen if isinstance(screen, dict) else {}
    if isinstance(body.get("content"), dict):
        body = body["content"]
    raw = body.get("elements")
    return [e for e in raw if isinstance(e, dict)] if isinstance(raw, list) else []


def fp_of(node: dict, nodes: list) -> dict:
    return screen_key.element_fp(node, ancestors_of(node, nodes))


def _same(a: object, b: object) -> float:
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
        total += weight * (_jaccard(a, b) if name == "anc_rids" else _same(a, b))
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


def _find(loc: str, screen: object) -> dict | None:
    """The unique node a locator names on *screen*, else ``None``."""
    from tools.mobile import actions

    kind, value = split_locator(loc)
    if not kind:
        return None
    target = actions.Target(**{kind: value})
    got = actions.resolve_target(target, screen)
    content = (got or {}).get("content") or {}
    element = content.get("element")
    return element if isinstance(element, dict) else None


def _row_for(ctx, step: object, key: str) -> dict | None:
    """The learned row naming this step's selector: this screen's rows first, then
    every row (an anchor rid rename changes the screen key, and that is a heal)."""
    snap = (getattr(ctx, "knowledge", None) or {}).get("snap") or {}
    table = snap.get("elements") or {}
    mine = set(own_locators(step))
    ordered = list(table.get(key) or [])
    for name, rows in table.items():
        if name != key:
            ordered.extend(rows or [])
    for row in ordered:
        if mine & {item["l"] for item in parse_queue(row.get("locators_json"))}:
            return row
    return None


def _pending(ctx, used: str, tried: list, fp: dict) -> None:
    knowledge_hooks.kbuf_add(
        ctx, {"kind": PENDING, "used": used, "tried": list(tried) + [fp_token(fp)]}
    )


def _record_default(ctx, step: object, nodes: list, default: dict) -> None:
    own = own_locators(step)
    if own:
        _pending(ctx, own[0], [], fp_of(default, nodes))


def _heal_locator(node: dict) -> str:
    for kind, field in (("rid", "rid"), ("text", "text"), ("text", "desc")):
        if node.get(field):
            return locator_of(kind, node[field])
    return ""


def _heal(ctx, row: dict, tried: list, nodes: list, screen: object):
    try:
        fp = json.loads(str(row.get("fp_json") or "{}"))
    except (ValueError, TypeError):
        return knowledge_hooks.NONE
    node = best_match(fp, nodes) if isinstance(fp, dict) and fp else None
    loc = _heal_locator(node) if node else ""
    if not loc:
        _pending(ctx, "", tried, fp if isinstance(fp, dict) else {})
        return knowledge_hooks.NONE
    knowledge_hooks.kbuf_add(ctx, {"kind": "heal", "to": loc, "tried": tried})
    _pending(ctx, HEAL_PREFIX + loc, tried, fp)
    return knowledge_hooks.Directive(
        kind="replace_target", target=node, fired={"kind": "heal", "locator": loc}
    )


async def resolve_target(ctx, step, screen, default):
    """Queue then heal, only when the default resolution missed a targeted step."""
    try:
        op = str(
            step.get("op") if isinstance(step, dict) else getattr(step, "op", "") or ""
        )
        if op in _SKIP_OPS or not own_locators(step):
            return knowledge_hooks.NONE
        nodes = _nodes(screen)
        if default is not None:
            _record_default(
                ctx, step, nodes, default if isinstance(default, dict) else {}
            )
            return knowledge_hooks.NONE
        key = screen_key.screen_key(
            screen,
            str(getattr(ctx, "package", "") or ""),
            str(getattr(ctx, "activity", "") or ""),
        ).key
        row = _row_for(ctx, step, key)
        if row is None:
            return knowledge_hooks.NONE
        return _from_queue(ctx, row, nodes, screen)
    except Exception:
        logger.exception("knowledge locators resolve_target failed")
        return knowledge_hooks.NONE


def _from_queue(ctx, row: dict, nodes: list, screen: object):
    tried: list = []
    for item in parse_queue(row.get("locators_json"))[
        : knowledge_limits.LOCATOR_QUEUE_MAX
    ]:
        node = _find(item["l"], screen)
        if node is not None:
            try:
                fp = json.loads(str(row.get("fp_json") or "{}"))
            except (ValueError, TypeError):
                fp = {}
            _pending(ctx, item["l"], tried, fp if isinstance(fp, dict) else {})
            return knowledge_hooks.Directive(
                kind="replace_target",
                target=node,
                fired={"kind": "queue", "locator": item["l"]},
            )
        tried.append(item["l"])
    return _heal(ctx, row, tried, nodes, screen)


def post_step(ctx, entry, before, after) -> None:
    """Copy the pending locator record onto the step entry (in memory only)."""
    buf = getattr(ctx, "kbuf", None)
    if not isinstance(buf, list) or not isinstance(entry, dict):
        return
    pending = [e for e in buf if isinstance(e, dict) and e.get("kind") == PENDING]
    if not pending:
        return
    buf[:] = [e for e in buf if not (isinstance(e, dict) and e.get("kind") == PENDING)]
    last = pending[-1]
    entry["locator_used"] = str(last.get("used") or "")[:200]
    entry["locator_tried"] = list(last.get("tried") or [])[:10]
