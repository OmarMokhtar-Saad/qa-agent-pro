"""Mining shortcut candidates out of one run's steps (split out of knowledge_stage_shortcuts)."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from tools.mobile import actions, app_knowledge, knowledge_limits

MAX_WINDOWS = 300
_OPS = ("tap", "type")


def _linked(a: dict, b: dict) -> bool:
    return a.get("after_screen_key") == b.get("screen_key")


def _eligible(rec: dict) -> bool:
    return (
        rec.get("outcome") == "ok"
        and rec.get("op") in _OPS
        and bool(rec.get("target_rid"))
        and not str(rec.get("screen_key", "")).startswith("sk1d:")
        and bool(rec.get("after_screen_key"))
    )


def chains(steps: Any) -> list:
    """Maximal runs of eligible steps where each ends on the next one's start screen."""
    out: list = []
    cur: list = []
    for rec in list(steps or []):
        if not _eligible(rec):
            if cur:
                out.append(cur)
            cur = []
            continue
        if cur and not _linked(cur[-1], rec):
            out.append(cur)
            cur = []
        cur.append(rec)
    if cur:
        out.append(cur)
    return out


def _shape(window: list) -> tuple[list, dict]:
    """The stored steps and args of *window*; typed text becomes ``{arg_n}``."""
    args: dict
    steps, args = [], {}
    for rec in window:
        step = {"op": rec["op"], "rid": rec["target_rid"]}
        if rec["op"] == "type":
            name = "arg_%d" % (len(args) + 1)
            step["arg"] = name
            field = str(rec.get("text_field") or "")
            if field:
                step["field"] = field
            args[name] = {"field": field}
        steps.append(step)
    return steps, args


def secret_refusal(steps: list) -> str:
    """Why this sequence must not be stored, or ``""``."""

    for step in steps:
        if step["op"] == "type":
            probe = {
                "op": "type",
                "target": {"rid": step["rid"]},
                "field": step.get("field", ""),
            }
            if actions.is_credential_action(probe):
                return "credential field"
    return app_knowledge.secret_reason(json.dumps(steps), (), "")


def _name(start: str, end: str, shaped: list) -> str:
    digest = hashlib.sha1(
        json.dumps([start, end, shaped], sort_keys=True).encode()
    ).hexdigest()
    return "mined-" + digest[:12]


def _scan_size(chain: list, size: int, seen: dict) -> int:
    refused = 0
    for i in range(len(chain) - size + 1):
        window = chain[i : i + size]
        start, end = window[0]["screen_key"], window[-1]["after_screen_key"]
        if start == end:
            continue
        shaped, args = _shape(window)
        if secret_refusal(shaped):
            refused += 1
            continue
        seen.setdefault(_name(start, end, shaped), (start, end, shaped, args))
        if len(seen) >= MAX_WINDOWS:
            break
    return refused


def candidates(steps: Any) -> tuple[list, int]:
    """``([(name, start, end, steps, args)], refused)`` mined from one run, longest first."""
    seen: dict = {}
    refused = 0
    lo, hi = knowledge_limits.SHORTCUT_MIN_LEN, knowledge_limits.SHORTCUT_MAX_LEN
    for chain in chains(steps):
        for size in range(min(hi, len(chain)), lo - 1, -1):
            refused += _scan_size(chain, size, seen)
    return [(n, *v) for n, v in seen.items()], refused
