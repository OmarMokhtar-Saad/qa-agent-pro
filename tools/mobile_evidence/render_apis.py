"""The run-level API surface: endpoints the app called, from its own log."""

from __future__ import annotations

from collections import Counter

from tools.mobile_evidence.render_esc import MAX_ROWS, ev_esc, status_label
from tools.mobile_evidence.render_html import empty
from tools.mobile_evidence.render_view import content_of, has_evidence, no_log_lead


def _endpoint_path(b: dict) -> str:
    """The path of one binding's URL, or its best stand-in."""
    url = str(b.get("url") or "")
    rest = url.split("://", 1)[1] if "://" in url else ""
    if "/" in rest:
        return "/" + rest.split("/", 1)[1].split("?")[0]
    return url or str(b.get("target") or "(unknown)")


def _new_row(verb: str, path: str) -> dict:
    return {
        "verb": verb,
        "path": path,
        "calls": 0,
        "cases": set(),
        "statuses": Counter(),
        "samples": [],
        "errors": 0,
    }


def _tally(row: dict, tc_id: object, b: dict) -> None:
    row["calls"] += 1
    row["cases"].add(tc_id)
    row["statuses"][status_label(b)] += 1
    if b.get("ok") is False:
        row["errors"] += 1
    ms = b.get("derivedMs")
    if isinstance(ms, (int, float)):
        row["samples"].append(float(ms))


def endpoint_rows(content: dict) -> list:
    """(verb, path) -> {calls, cases:set, statuses:Counter, samples:list} over paired turns."""
    agg: dict = {}
    for tc_id, view in (content.get("by_case") or {}).items():
        for b in view["bindings"]:
            path = _endpoint_path(b)
            key = ((b.get("verb") or "CALL").upper(), path)
            row = agg.setdefault(key, _new_row(key[0], path))
            _tally(row, tc_id, b)
    return sorted(agg.values(), key=lambda r: (-r["calls"], r["path"]))


def endpoint_table_row(r: dict) -> str:
    return (
        '<tr><td class="uc"><span class="cap">'
        + ev_esc(r["verb"], 12)
        + "</span> <code>"
        + ev_esc(r["path"], 160)
        + "</code></td><td>"
        + str(r["calls"])
        + "</td><td>"
        + str(len(r["cases"]))
        + "</td><td>"
        + ", ".join(
            ev_esc(label, 40) + " ×" + str(n)
            for label, n in r["statuses"].most_common()
        )
        + "</td></tr>"
    )


def hosts_hint(hosts: list) -> str:
    if not hosts:
        return ""
    return (
        '<p class="hint">real wire, host'
        + ("" if len(hosts) == 1 else "s")
        + " "
        + ", ".join(ev_esc(h, 80) for h in hosts)
        + "</p>"
    )


def apis_section(loaded: object) -> str:
    content = content_of(loaded)
    if not has_evidence(loaded):
        return empty(
            no_log_lead(content)
            + ", so no endpoint is known: "
            + str(content.get("reason") or "")
        )
    rows = endpoint_rows(content)
    if not rows:
        return empty("the app made no endpoint call inside any case window")
    hosts = (content.get("integrity") or {}).get("hosts") or []
    body = "".join(endpoint_table_row(r) for r in rows[:MAX_ROWS])
    return (
        hosts_hint(hosts)
        + '<div class="tablewrap"><table class="cov"><thead><tr><th scope="col">Endpoint</th>'
        '<th scope="col">Calls</th><th scope="col">Cases</th><th scope="col">Statuses</th></tr></thead><tbody>'
        + body
        + "</tbody></table></div>"
    )
