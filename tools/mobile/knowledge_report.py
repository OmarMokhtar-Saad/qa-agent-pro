"""The report's "what we learned" section (contract 1.8).

``knowledge_section(run_id)`` collects Items from the provider modules named in
``SECTION_PROVIDERS`` (each exposes ``build(package, run_id, summary)``); sync,
bounded, never raising. ``render_section`` turns it into HTML with
``report.esc`` for every stored string, and is ``""`` when there is nothing.

Item = {"table", "id", "title", "detail", "action_hint"}.
"""

from __future__ import annotations

import importlib
import json
import logging
import sqlite3
from contextlib import closing
from typing import NamedTuple

from tools.mobile import app_knowledge, run_store
from tools.mobile import knowledge_limits as limits
from tools.mobile.report_text import esc

logger = logging.getLogger(__name__)

SECTION_PROVIDERS = ("knowledge_review_learned", "knowledge_review_review")
#: provider module -> the section key its items fill.
_PROVIDER_KEY = {
    "knowledge_review_learned": "learned",
    "knowledge_review_review": "review",
}
_TEXT = 300


def _empty() -> dict:
    return {"learned": [], "guards": [], "review": [], "recheck": 0, "skipped": []}


def package_of(run_id: str) -> str:

    manifest = (run_store.read_manifest(run_id) or {}).get("content")
    return (
        str((manifest or {}).get("package") or "") if isinstance(manifest, dict) else ""
    )


def summary_of(package: str, run_id: str) -> dict:
    """``runs.summary_json`` for the run; ``{}`` when the store, table or row is absent."""
    try:
        path = app_knowledge.db_path(package)
        if path is None or not path.exists():
            return {}
        with closing(app_knowledge._connect(path, write=False)) as conn:
            row = conn.execute(
                "SELECT summary_json FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        loaded = json.loads(row[0]) if row and row[0] else {}
        return loaded if isinstance(loaded, dict) else {}
    except (OSError, ValueError, sqlite3.Error):
        return {}


def _item(raw: object) -> dict | None:
    if not isinstance(raw, dict):
        return None
    out = {
        key: str(raw.get(key) if raw.get(key) is not None else "")[:_TEXT]
        for key in ("table", "id", "title", "detail", "action_hint")
    }
    return out if out["title"] or out["detail"] else None


class _Call(NamedTuple):
    package: str
    run_id: str
    summary: dict


def _load_provider(section: dict, name: str, call: _Call) -> None:
    try:
        module = importlib.import_module("tools.mobile." + name)
        key = _PROVIDER_KEY[name]
        items = [_item(i) for i in module.build(*call) or []]
        section[key] = [i for i in items if i][: limits.LIST_PAGE_ROWS]
        guards = getattr(module, "guards", None)
        if guards:
            got = [_item(i) for i in guards(call.run_id) or []]
            section["guards"] = [i for i in got if i][: limits.LIST_PAGE_ROWS]
    except Exception:
        logger.exception("report provider %s failed", name)


def _totals(section: dict, summary: dict) -> None:
    try:
        section["recheck"] = max(0, int(summary.get("recheck") or 0))
    except (TypeError, ValueError, OverflowError):
        pass
    skipped = summary.get("skipped")
    if isinstance(skipped, list):
        section["skipped"] = [str(s)[:_TEXT] for s in skipped[: limits.LIST_PAGE_ROWS]]


def knowledge_section(run_id: object) -> dict:
    section = _empty()
    try:
        rid = str(run_id or "")
        package = package_of(rid)
        if not package:
            return section
        summary = summary_of(package, rid)
        for name in SECTION_PROVIDERS:
            _load_provider(section, name, _Call(package, rid, summary))
        _totals(section, summary)
    except Exception:
        logger.exception("knowledge_section failed")
        return _empty()
    return section


def _list(heading: str, items: list, esc) -> str:
    if not items:
        return ""
    rows = []
    for item in items:
        line = esc(item.get("title"), _TEXT)
        if item.get("detail"):
            line += ": " + esc(item["detail"], _TEXT)
        if item.get("action_hint"):
            line += " (" + esc(item["action_hint"], _TEXT) + ")"
        rows.append("<li>" + line + "</li>")
    return (
        '<p class="elab">'
        + esc(heading, 60)
        + '</p><ul class="plain">'
        + "".join(rows)
        + "</ul>"
    )


def _recheck_p(body: dict, esc) -> str:
    try:
        recheck = int(body.get("recheck") or 0)
    except (TypeError, ValueError, OverflowError):
        recheck = 0
    if recheck <= 0:
        return ""
    return '<p class="elab">' + esc("%d note(s) need a re-check" % recheck, 80) + "</p>"


def _parts(body: dict, esc) -> list:
    skipped = [{"title": s} for s in body.get("skipped") or []]
    return [
        _list("learned this run", body.get("learned") or [], esc),
        _list("notes that fired", body.get("guards") or [], esc),
        _list("needs your review", body.get("review") or [], esc),
        _list("skipped notes", skipped, esc),
        _recheck_p(body, esc),
    ]


def render_section(section: object) -> str:
    try:
        body = section if isinstance(section, dict) else {}

        inner = "".join(p for p in _parts(body, esc) if p)
        return '<div class="card">' + inner + "</div>" if inner else ""
    except Exception:
        logger.exception("render_section failed")
        return ""
