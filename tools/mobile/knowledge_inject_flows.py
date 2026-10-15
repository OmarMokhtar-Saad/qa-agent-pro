"""Inject source flows: tell the model which saved flows the app already has."""

from __future__ import annotations

from tools.mobile import knowledge_inject, kstg_flow_packets

_PRIO = 1


_HOW = (
    " The server replays it by itself when the app is on its first screen; "
    "flow=%s replays it on demand, flow=none turns auto-replay off."
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


def flows(snap: dict, key) -> list:
    """One item per saved flow (its packets grouped), in the stable block only."""
    if key is not None:
        return []
    rows = [r for r in snap.get("flows") or [] if isinstance(r, dict) and r.get("name")]
    return [_item(base, got) for base, got in _groups(rows).items()]


knowledge_inject.register_source("flows", flows)
