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

from tools.mobile import app_knowledge
from tools.mobile import knowledge_limits as limits

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


def _package(run_id: str) -> str:
    from tools.mobile import run_store

    manifest = (run_store.read_manifest(run_id) or {}).get("content")
    return (
        str((manifest or {}).get("package") or "") if isinstance(manifest, dict) else ""
    )


def _summary(package: str, run_id: str) -> dict:
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


def knowledge_section(run_id: object) -> dict:
    section = _empty()
    try:
        rid = str(run_id or "")
        package = _package(rid)
        if not package:
            return section
        summary = _summary(package, rid)
        for name in SECTION_PROVIDERS:
            try:
                module = importlib.import_module("tools.mobile." + name)
                key = _PROVIDER_KEY[name]
                items = [_item(i) for i in module.build(package, rid, summary) or []]
                section[key] = [i for i in items if i][: limits.LIST_PAGE_ROWS]
                guards = getattr(module, "guards", None)
                if guards:
                    got = [_item(i) for i in guards(rid) or []]
                    section["guards"] = [i for i in got if i][: limits.LIST_PAGE_ROWS]
            except Exception:
                logger.exception("report provider %s failed", name)
        try:
            section["recheck"] = max(0, int(summary.get("recheck") or 0))
        except (TypeError, ValueError, OverflowError):
            pass
        skipped = summary.get("skipped")
        if isinstance(skipped, list):
            section["skipped"] = [
                str(s)[:_TEXT] for s in skipped[: limits.LIST_PAGE_ROWS]
            ]
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


def render_section(section: object) -> str:
    try:
        body = section if isinstance(section, dict) else {}
        from tools.mobile.report import esc

        parts = [
            _list("learned this run", body.get("learned") or [], esc),
            _list("notes that fired", body.get("guards") or [], esc),
            _list("needs your review", body.get("review") or [], esc),
        ]
        skipped = [{"title": s} for s in body.get("skipped") or []]
        parts.append(_list("skipped notes", skipped, esc))
        try:
            recheck = int(body.get("recheck") or 0)
        except (TypeError, ValueError, OverflowError):
            recheck = 0
        if recheck > 0:
            parts.append(
                '<p class="elab">'
                + esc("%d note(s) need a re-check" % recheck, 80)
                + "</p>"
            )
        inner = "".join(p for p in parts if p)
        return '<div class="card">' + inner + "</div>" if inner else ""
    except Exception:
        logger.exception("render_section failed")
        return ""
