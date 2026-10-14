"""Locator resolution: the learned queue, then the similarity heal."""

from __future__ import annotations

import json
from typing import Any, NamedTuple

from tools.mobile import actions, knowledge_hooks, knowledge_limits, screen_key
from tools.mobile.kplg_calls import Resolve
from tools.mobile.kplg_loc_fp import (
    _heal_locator,
    _nodes,
    best_match,
    fp_of,
)
from tools.mobile.kplg_loc_str import (
    HEAL_PREFIX,
    PENDING,
    fp_token,
    own_locators,
    parse_queue,
    split_locator,
)

_SKIP_OPS = frozenset({"assert", "scroll", "wait", "done", "ask_tester"})


class Mark(NamedTuple):
    used: str
    tried: list
    fp: dict


class Scene(NamedTuple):
    nodes: list
    screen: Any


class Miss(NamedTuple):
    tried: list
    nodes: list


class Found(NamedTuple):
    loc: str
    node: dict
    tried: list


def _find(loc: str, screen: Any) -> dict | None:
    """The unique node a locator names on *screen*, else ``None``."""

    kind, value = split_locator(loc)
    if not kind:
        return None
    target = actions.Target(**{kind: value})
    got = actions.resolve_target(target, screen)
    content = (got or {}).get("content") or {}
    element = content.get("element")
    return element if isinstance(element, dict) else None


def _row_for(ctx, step: Any, key: str) -> dict | None:
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


def _pending(ctx, mark: Mark) -> None:
    knowledge_hooks.kbuf_add(
        ctx,
        {
            "kind": PENDING,
            "used": mark.used,
            "tried": list(mark.tried) + [fp_token(mark.fp)],
        },
    )


def _row_fp(row: dict) -> dict | None:
    """The row's fingerprint (``{}`` when not a dict); ``None`` when unparseable."""
    try:
        fp = json.loads(str(row.get("fp_json") or "{}"))
    except (ValueError, TypeError):
        return None
    return fp if isinstance(fp, dict) else {}


def _record_default(ctx, step: Any, fp: dict) -> None:
    own = own_locators(step)
    if own:
        _pending(ctx, Mark(own[0], [], fp))


def _heal(ctx, row: dict, miss: Miss):
    fp = _row_fp(row)
    if fp is None:
        return knowledge_hooks.NONE
    node = best_match(fp, miss.nodes) if fp else None
    loc = _heal_locator(node) if node else ""
    if not loc:
        _pending(ctx, Mark("", miss.tried, fp))
        return knowledge_hooks.NONE
    knowledge_hooks.kbuf_add(ctx, {"kind": "heal", "to": loc, "tried": miss.tried})
    _pending(ctx, Mark(HEAL_PREFIX + loc, miss.tried, fp))
    return knowledge_hooks.Directive(
        kind="replace_target", target=node, fired={"kind": "heal", "locator": loc}
    )


def _hit(ctx, row: dict, found: Found):
    _pending(ctx, Mark(found.loc, found.tried, _row_fp(row) or {}))
    return knowledge_hooks.Directive(
        kind="replace_target",
        target=found.node,
        fired={"kind": "queue", "locator": found.loc},
    )


def _from_queue(ctx, row: dict, scene: Scene):
    tried: list = []
    for item in parse_queue(row.get("locators_json"))[
        : knowledge_limits.LOCATOR_QUEUE_MAX
    ]:
        node = _find(item["l"], scene.screen)
        if node is not None:
            return _hit(ctx, row, Found(item["l"], node, tried))
        tried.append(item["l"])
    return _heal(ctx, row, Miss(tried, scene.nodes))


def _op_of(step: Any) -> str:
    return str(
        step.get("op") if isinstance(step, dict) else getattr(step, "op", "") or ""
    )


def _key_of(ctx, screen: Any) -> str:
    return screen_key.screen_key(
        screen,
        str(getattr(ctx, "package", "") or ""),
        str(getattr(ctx, "activity", "") or ""),
    ).key


def resolve(ctx, call: Resolve):
    """Queue then heal, only when the default resolution missed a targeted step."""
    if _op_of(call.step) in _SKIP_OPS or not own_locators(call.step):
        return knowledge_hooks.NONE
    nodes = _nodes(call.screen)
    if call.default is not None:
        default = call.default if isinstance(call.default, dict) else {}
        _record_default(ctx, call.step, fp_of(default, nodes))
        return knowledge_hooks.NONE
    row = _row_for(ctx, call.step, _key_of(ctx, call.screen))
    if row is None:
        return knowledge_hooks.NONE
    return _from_queue(ctx, row, Scene(nodes, call.screen))
