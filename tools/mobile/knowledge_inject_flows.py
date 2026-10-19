"""Inject source flows: tell the model which saved flows the app already has."""

from __future__ import annotations

from tools.mobile import flow_goal_fit, knowledge_inject, kstg_flow_packets

_PRIO = 1


_HOW = (
    " The server replays it by itself when the app is on its first screen "
    "(at the start of a case, after a stopped step, and in an explore run); "
    "flow=%s replays it on demand. flow=none turns auto-replay off for the "
    "run in a test run; an explore run honours it only when the user asked "
    "for no flow."
)


def _text(row: dict) -> str:
    fields = row.get("fields") or []
    secret = "; secret fields: %s" % ", ".join(map(str, fields)) if fields else ""
    covers = str(row.get("covers") or "")
    span = " It %s." % covers if covers else ""
    head = "known flow available: %s (%s steps%s).%s" % (
        row["name"],
        row.get("steps"),
        secret,
        span,
    )
    return head + _HOW % row["name"]


def _packet_text(rows: list) -> str:
    names = [str(r["name"]) for r in rows]
    return (
        "known flow available in %d packets: %s. Replay them in order, one "
        "flow=<name> call each, the next only after the one before it finished."
        % (len(names), ", ".join(names))
    )


def _groups(rows: list) -> dict:
    """Saved flows by packet base (a plain flow is its own base), packets in order."""
    out: dict = {}
    for row in rows:
        hit = kstg_flow_packets.PACKET_RE.match(str(row["name"]))
        out.setdefault(hit.group("base") if hit else row["name"], []).append(row)
    for got in out.values():
        got.sort(key=lambda r: (len(str(r["name"])), str(r["name"])))
    return out


def _item(base: str, rows: list) -> dict:
    plain = len(rows) == 1 and rows[0]["name"] == base
    return {
        "id": "flows:%s" % base,
        "table": "flows",
        "prio": _PRIO,
        "text": _text(rows[0]) if plain else _packet_text(rows),
    }


def _rank(got: list, goal: str = "") -> tuple:
    """Best-first order of one packet group: reached, good replays, goal fit, score, route.

    Proven beats untried; the goal fit only breaks a tie between equally proven routes."""
    first = got[0]
    return (
        bool(first.get("reached")),
        int(first.get("ok") or 0),
        flow_goal_fit.fit(goal, first.get("goal")),
        float(first.get("score") or 0),
        sum(int(r.get("steps") or 0) for r in got),
    )


def _best_per_goal(groups: dict, run: str = "") -> dict:
    """Keep one group per route shape (else per goal; a legacy flow is its own)."""
    best: dict = {}
    for base, got in groups.items():
        shape = str(got[0].get("shape") or got[0].get("goal") or "") or "base:" + base
        if shape not in best or _rank(got, run) > _rank(best[shape][1], run):
            best[shape] = (base, got)
    return dict(best.values())


def _offerable(snap: dict) -> list:
    """Saved flows worth offering: named, not inert (no required step), not dead (0 ok)."""
    rows = [r for r in snap.get("flows") or [] if isinstance(r, dict) and r.get("name")]
    return [r for r in rows if not r.get("inert") and not r.get("dead")]


def _why(got: list) -> str:
    first = got[0]
    bits = [
        "it reached its goal" if first.get("reached") else "the longest known route"
    ]
    if int(first.get("ok") or 0):
        bits.append("%d good replays" % int(first["ok"]))
    return " Offered because " + " and ".join(bits) + "."


def flows(snap: dict, key) -> list:
    """ONE item: the best saved flow (packets grouped), said why; stable block only."""
    if key is not None:
        return []
    run = str(snap.get("run_goal") or "")
    best = _best_per_goal(_groups(_offerable(snap)), run)
    if not best:
        return []
    base, got = max(best.items(), key=lambda kv: _rank(kv[1], run))
    item = _item(base, got)
    return [dict(item, text=item["text"] + _why(got))]


knowledge_inject.register_source("flows", flows)
