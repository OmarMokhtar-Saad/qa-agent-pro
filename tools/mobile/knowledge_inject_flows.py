"""Inject source flows: tell the model which saved flows the app already has."""

from __future__ import annotations

from tools.mobile import knowledge_inject

_PRIO = 1


def _text(row: dict) -> str:
    fields = row.get("fields") or []
    secret = "; secret fields: %s" % ", ".join(map(str, fields)) if fields else ""
    return "known flow available: %s (%s steps%s). Replay it with flow=%s." % (
        row["name"],
        row.get("steps"),
        secret,
        row["name"],
    )


def flows(snap: dict, key) -> list:
    """One item per saved flow, in the stable block only (``key`` is None)."""
    if key is not None:
        return []
    return [
        {
            "id": "flows:%s" % row["name"],
            "table": "flows",
            "prio": _PRIO,
            "text": _text(row),
        }
        for row in snap.get("flows") or []
        if isinstance(row, dict) and row.get("name")
    ]


knowledge_inject.register_source("flows", flows)
