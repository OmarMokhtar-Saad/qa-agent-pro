"""Locator strings (``rid:``/``label:``/``text:``) and fingerprint tokens."""

from __future__ import annotations

import json
from typing import Any

HEAL_PREFIX = "heal:"
FP_PREFIX = "fp:"
PENDING = "locator_pending"
_KINDS = ("rid", "label", "text")
_LOCATOR_CLIP = 150


def locator_of(kind: str, value: Any) -> str:
    return "%s:%s" % (kind, str(value or "")[:_LOCATOR_CLIP])


def split_locator(loc: Any) -> tuple:
    kind, _, value = str(loc or "").partition(":")
    return (kind, value) if kind in _KINDS and value else ("", "")


def own_locators(step: Any) -> list:
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


def parse_queue(raw: Any) -> list:
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
