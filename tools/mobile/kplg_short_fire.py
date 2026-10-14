"""Shortcuts plug firing: match the learned sequence and insert it as one directive."""

from __future__ import annotations

from typing import Any, NamedTuple

from tools.mobile import actions, knowledge_hooks, knowledge_limits
from tools.mobile.knowledge_hooks import Directive
from tools.mobile.knowledge_plug_popups import op_rid, screen_rids


class Window(NamedTuple):
    screen: Any
    ahead: list


class Fired(NamedTuple):
    item: str
    parsed: list
    consume: int


def _count(ctx: Any, kind: str, item: str) -> int:
    return sum(
        1
        for ev in getattr(ctx, "kbuf", None) or []
        if isinstance(ev, dict) and ev.get("kind") == kind and ev.get("item") == item
    )


def _body(step: Any) -> dict:
    if isinstance(step, dict):
        return step
    dump = getattr(step, "model_dump", None)
    return dump(mode="json") if dump else {}


def _build(steps: list, ahead: list) -> list | None:
    """Action dicts for the shortcut steps; a ``type`` step takes its text, secret
    flag and field from the model's matching step. ``None`` when one cannot be filled."""
    out: list = []
    for i, st in enumerate(steps):
        action = {"op": st["op"], "target": {"rid": st["rid"]}}
        if st["op"] == "type":
            if i >= len(ahead):
                return None
            mine = _body(ahead[i])
            for key in ("text", "secret", "field"):
                if mine.get(key) not in (None, ""):
                    action[key] = mine[key]
        out.append(action)
    return out


def _fail(ctx: Any, item: str, why: str) -> None:
    knowledge_hooks.kbuf_add(ctx, {"kind": "shortcut_fail", "item": item, "why": why})


def _fresh(ctx: Any, item: str, steps: list) -> bool:
    return bool(steps) and not (
        _count(ctx, "shortcut_fired", item) or _count(ctx, "shortcut_fail", item)
    )


def _aligned(steps: list, ahead: list) -> bool:
    if len(ahead) < len(steps):
        # Fewer scripted steps left than the shortcut stands in for: it would
        # run steps the script never asked for.
        return False
    return op_rid(ahead[0]) == (steps[0].get("op"), steps[0].get("rid"))


def _mismatch(row: dict, win: Window) -> str:
    steps = row.get("steps") or []
    here = screen_rids(win.screen)
    need = [str(r) for r in (row.get("precondition") or {}).get("rids") or []]
    if any(r not in here and r.split("/", 1)[-1] not in here for r in need):
        return "precondition"
    for i in range(1, len(steps)):
        if op_rid(win.ahead[i]) != (steps[i].get("op"), steps[i].get("rid")):
            return "sequence"
    return ""


def _parsed(steps: list, ahead: list) -> list | None:
    built = _build(steps, ahead)
    if not built or len(built) > knowledge_limits.PRECONDITION_STEPS * 4:
        return None

    script = actions.parse_script({"actions": built}).get("content")
    parsed = list(getattr(script, "actions", None) or [])
    return parsed if len(parsed) == len(built) else None


def _fire(ctx: Any, fired: Fired) -> Directive:
    expect = [list(op_rid(p)) for p in fired.parsed]
    knowledge_hooks.kbuf_add(
        ctx,
        {
            "kind": "shortcut_fired",
            "item": fired.item,
            "left": len(fired.parsed),
            "expect": expect,
        },
    )
    return Directive(
        kind="insert_steps",
        steps=fired.parsed,
        reason="shortcut",
        fired={"item": fired.item, "kind": "shortcut", "consume": fired.consume},
    )


def _try(ctx: Any, row: dict, win: Window) -> Directive:
    item = "shortcuts:%s" % row.get("id")
    steps = row.get("steps") or []
    if not (_fresh(ctx, item, steps) and _aligned(steps, win.ahead)):
        return knowledge_hooks.NONE
    why = _mismatch(row, win)
    if why:
        _fail(ctx, item, why)
        return knowledge_hooks.NONE
    parsed = _parsed(steps, win.ahead)
    if parsed is None:
        return knowledge_hooks.NONE
    return _fire(ctx, Fired(item, parsed, len(steps)))


def _live(ctx: Any) -> dict | None:
    live = [
        ev
        for ev in getattr(ctx, "kbuf", None) or []
        if isinstance(ev, dict)
        and ev.get("kind") == "shortcut_fired"
        and not ev.get("refused")
        and int(ev.get("left") or 0) > 0
    ]
    return live[-1] if live else None


def _advance(ev: dict, entry: dict) -> None:
    expect = ev.get("expect") or []
    at = len(expect) - int(ev["left"])
    if not 0 <= at < len(expect) or list(op_rid(entry.get("action") or {})) != list(
        expect[at]
    ):
        return
    if entry.get("outcome") != "ok":
        ev.update(kind="shortcut_fail", why="steps", left=0)
        return
    ev["left"] = int(ev["left"]) - 1
