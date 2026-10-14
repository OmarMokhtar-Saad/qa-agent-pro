"""The toolbar groups, extra sorts and per-card data and metrics."""

from __future__ import annotations

from collections import Counter

from tools.mobile_evidence import model
from tools.mobile_evidence import render_esc as esc
from tools.mobile_evidence.render_html import Chip, chip, metric, metric_pair
from tools.mobile_evidence.render_view import content_of, has_evidence, view_of

_SOURCES = (
    ("app", "the app’s own log"),
    ("none", "no app evidence"),
)


def _tally(facts: list, by_case: dict) -> tuple[Counter, Counter]:
    models: Counter = Counter()
    sources: Counter = Counter()
    for f in facts:
        view = by_case.get(str(f.get("tc_id") or ""))
        if view and view["llm"]:
            for name in view["models"]:
                models[name] += 1
            sources["app"] += 1
        else:
            sources["none"] += 1
    return models, sources


def toolbar_groups(loaded: object, facts: list) -> list:
    content = content_of(loaded)
    models, sources = _tally(facts, content.get("by_case") or {})
    groups = []
    if models:
        chips = (chip(Chip("model", name, name, n)) for name, n in models.most_common())
        groups.append(("Model", "".join(chips)))
    if has_evidence(loaded):
        chips = (
            chip(Chip("src", key, label, sources[key]))
            for key, label in _SOURCES
            if sources.get(key)
        )
        groups.append(("Evidence", "".join(chips)))
    return groups


def extra_sorts(loaded: object) -> list:
    if not has_evidence(loaded):
        return []
    return [
        ("exch", "exchanges, most first"),
        ("tok", "tokens, most first"),
        ("cost", "cost, highest first"),
    ]


def card_data(loaded: object, tc_id: object) -> dict:
    view = view_of(loaded, tc_id)
    if not view:
        return {"data-src": "none"} if has_evidence(loaded) else {}
    return {
        "data-model": ", ".join(sorted(view["models"])) if view["models"] else "",
        "data-src": "app" if view["llm"] or view["bindings"] else "none",
        "data-exch": str(view["exch"]),
        "data-tok": str(view["in"] + view["out"]) if view["llm"] else "",
        "data-cost": ("%.6f" % view["cost"]) if view["cost"] is not None else "",
    }


def _cost_bits(view: dict) -> list:
    label = (
        "total cost"
        if not view["unpriced"]
        else "total cost (" + str(view["unpriced"]) + " unpriced)"
    )
    return [esc.MSEP, metric(model.money(view["cost"]), label, "cost")]


def card_metrics(loaded: object, tc_id: object) -> list:
    view = view_of(loaded, tc_id)
    if not view or not view["exch"]:
        return []
    bits = [
        esc.MSEP,
        metric(len(view["llm"]), "LLM"),
        metric(len(view["bindings"]), "API"),
        metric(len(view["tools"]), "tools"),
    ]
    if view["llm"]:
        tok = metric_pair(
            (format(view["in"], ","), "tok in"),
            (format(view["out"], ","), "out"),
            "tok",
        )
        bits += [esc.MSEP, tok]
    if view["cost"] is not None:
        bits += _cost_bits(view)
    return bits
