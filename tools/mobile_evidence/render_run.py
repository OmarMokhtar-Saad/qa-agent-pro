"""Run-level fragments: the runstrip cells and the overview tiles."""

from __future__ import annotations

from collections import Counter
from typing import Any

from tools.mobile_evidence import evidence, model
from tools.mobile_evidence.render_block import Kpi, gap_kpi, kpi
from tools.mobile_evidence.render_esc import ev_esc
from tools.mobile_evidence.render_view import content_of, has_evidence


def facts_cells(loaded: object) -> list:
    """Runstrip cells: ``[(label, value_html, sub_html, tone)]``."""
    content = content_of(loaded)
    if not has_evidence(loaded):
        reason = ev_esc(content.get("reason") or "not captured", 160)
        return [("app evidence", "none", reason, "void")]
    cells = [
        _backend_cell(content.get("integrity") or {}),
        _model_cell(content["evidence"]),
        _evidence_cell(content),
        _clock_cell(content.get("clock_missing") or []),
    ]
    return [cell for cell in cells if cell is not None]


def _backend_sub(integrity: dict) -> str:
    if integrity.get("envDisagrees"):
        return (
            "manifest says "
            + ev_esc(integrity.get("envClaimed"), 24)
            + " — the traffic is the authority"
        )
    if integrity.get("envFromTraffic"):
        return "from the traffic"
    return "as the app's manifest says"


def _backend_cell(integrity: dict) -> tuple | None:
    env = integrity.get("envFromTraffic") or integrity.get("envClaimed")
    if not env:
        return None
    tone = "gap" if integrity.get("envDisagrees") else ""
    return ("backend", ev_esc(env, 40), _backend_sub(integrity), tone)


def _model_cell(ev: Any) -> tuple | None:
    served: Counter = Counter()
    for row in ev.models():
        served[row["model"]] += row["calls"]
    if not served:
        return None
    names = ", ".join(ev_esc(name, 60) for name, _n in served.most_common(3))
    calls = sum(served.values())
    return (
        "model",
        names,
        str(calls) + " model call" + ("" if calls == 1 else "s"),
        "",
    )


def _evidence_cell(content: dict) -> tuple:
    source = content.get("source")
    sub = (
        ev_esc(content.get("reason"), 160)
        if content.get("reason")
        else "pulled with run-as after the run"
    )
    return (
        "evidence",
        "the app’s own event log" if source == "ndjson" else "logcat slices only",
        sub,
        "ok" if source == "ndjson" else "gap",
    )


def _clock_cell(missing: list) -> tuple | None:
    if not missing:
        return None
    n = len(missing)
    return (
        "device clock",
        "not read on " + str(n) + " case" + ("" if n == 1 else "s"),
        ev_esc(evidence.NO_CLOCK_NOTE, 160),
        "gap",
    )


def overview_tiles(loaded: object) -> list:
    content = content_of(loaded)
    if not has_evidence(loaded):
        reason = content.get("reason") or "not captured"
        return [gap_kpi("app evidence", reason, key="evidence")]
    ev = content["evidence"]
    totals = ev.totals()
    return _count_tiles(totals) + [_spend_tile(ev, content, totals)]


def _llm_tile(totals: dict) -> str:
    between = (
        " · " + str(totals["ambiguous"]) + " between two cases, on neither card"
        if totals.get("ambiguous")
        else ""
    )
    detail = "model round-trips the app made across the cases" + between
    return kpi(Kpi(str(totals["llm"]), "LLM requests", detail, "llm"))


def _tokens_tile(totals: dict) -> str:
    detail = format(int(totals["out"]), ",") + " out — as the provider reported them"
    return kpi(Kpi(format(int(totals["in"]), ","), "tokens in", detail, "tokens"))


def _count_tiles(totals: dict) -> list:
    return [
        _llm_tile(totals),
        kpi(
            Kpi(
                str(totals["bindings"]),
                "API exchanges",
                "endpoint calls the app made",
                "api",
            )
        ),
        kpi(Kpi(str(totals["tools"]), "tool calls", "tools the model invoked", "tool")),
        _tokens_tile(totals),
    ]


def _price_rows(ev: Any, content: dict) -> tuple[float, int, int]:
    spend = 0.0
    priced = unpriced = 0
    for row in ev.models():
        count = model.TokenCount(row["in"], row["out"])
        usd = model.price(row["model"], count, content.get("rates"))
        if usd is None:
            unpriced += row["calls"]
        else:
            priced += row["calls"]
            spend += usd
    return spend, priced, unpriced


def _spend_detail(content: dict, unpriced: int) -> str:
    detail = ev_esc(
        content.get("rates_note") or "at the profile's published rates", 200
    )
    if unpriced:
        detail += " · " + str(unpriced) + " call" + ("" if unpriced == 1 else "s")
        detail += " unpriced"
    return detail


def _spend_tile(ev: Any, content: dict, totals: dict) -> str:
    spend, priced, unpriced = _price_rows(ev, content)
    if priced:
        detail = _spend_detail(content, unpriced)
        return kpi(
            Kpi(ev_esc(model.money(spend), 24), "estimated spend", detail, "cost")
        )
    why = (
        "unpriced — no model in this run is in the profile's rates table"
        if totals["llm"]
        else "no model call to price"
    )
    return gap_kpi("estimated spend", why, key="cost")
