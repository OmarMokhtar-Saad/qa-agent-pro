"""``qa_mobile_knowledge`` read actions: notes list/retire, list and show."""

from __future__ import annotations

import json
from contextlib import closing
from typing import Any

from tools.mobile import app_knowledge, knowledge_limits
from tools.mobile import knowledge_db as kdb
from tools.mobile.ktool_args import Page, Retire
from tools.mobile.ktool_core import _ROW_CHARS, _WARN, _refuse
from tools.untrusted import single_line, wrap_untrusted


def notes_list(package: str) -> str:
    """The slice-1 ``qa_mobile_notes list`` reply, byte for byte."""
    listed = app_knowledge.list_notes(package)
    if listed.get("error"):
        return _WARN + single_line(listed["error"], 300)
    return app_knowledge.render(package, listed["content"])


def notes_retire(retire: Retire) -> str:
    """The slice-1 ``qa_mobile_notes retire`` reply, byte for byte."""
    why = app_knowledge.Retirement(str(retire.reason or ""), retire.run_id)
    done = app_knowledge.retire_note(retire.package, retire.note_id, why)
    if done.get("error"):
        return _WARN + single_line(done["error"], 300)
    return "Retired app note #%s for `%s`. It stays in history." % (
        retire.note_id,
        retire.package,
    )


def _screen_inputs(reason: str, text: str, payload: str) -> str:
    """A refusal message, or ``""`` when the free-text inputs are acceptable."""
    if len(payload or "") > knowledge_limits.PAYLOAD_CHARS:
        return "`payload` is too large"
    if (payload or "").strip():
        try:
            json.loads(payload)
        except ValueError:
            return "`payload` must be JSON"
    # payload is screened per row (import) or per op (reflect_submit), not whole:
    # an export carries timestamps that look like digit-run secrets.
    for name, value in (("reason", reason), ("text", text)):
        if value and app_knowledge.secret_reason(value):
            return "`%s` looks like it holds a secret, so it was not used" % name
    return ""


def _read_rows(package: str, table: str, query: kdb.Where):
    path = app_knowledge.db_path(package)
    if path is None:
        return None, "`package` is not an Android package name"
    if not path.exists():
        return [], ""
    with closing(app_knowledge._connect(path, write=False)) as conn:
        return kdb.rows(conn, table, query), ""


def _render_row(table: str, row: dict) -> str:
    skip = {"id"}
    body = ", ".join(
        "%s=%s" % (k, single_line(v, 120))
        for k, v in row.items()
        if k not in skip and v not in (None, "")
    )
    return "%s:%s %s" % (table, row.get("id"), body[:_ROW_CHARS])


def _page_text(package: str, table: str, shown: Page) -> str:
    body = "\n".join(_render_row(table, r) for r in shown.window)
    out = "%s for `%s` (page %d):\n%s" % (
        table,
        package,
        max(0, int(shown.page or 0)),
        wrap_untrusted(
            "app_knowledge_" + table, body, limit=knowledge_limits.STABLE_BLOCK_CHARS
        ),
    )
    return out + (
        "\nMore: pass page=%d." % (int(shown.page or 0) + 1) if shown.more else ""
    )


def list_table(package: str, table: str, page: int) -> str:
    if table == "notes":
        return notes_list(package)
    size = knowledge_limits.LIST_PAGE_ROWS
    start = max(0, int(page or 0)) * size
    found, problem = _read_rows(package, table, kdb.Where("", (), start + size + 1))
    if problem:
        return _refuse(problem)
    window = (found or [])[start : start + size]
    if not window:
        return "No %s saved for `%s` yet." % (table, package)
    more = len(found) > start + size
    return _page_text(package, table, Page(window, page, more))


def _show_id(table: str, item_id: Any):
    """``(table, number)``, or the refusal text when ``item_id`` is unusable."""
    parsed = (
        kdb.parse_item_id(item_id)
        if isinstance(item_id, str) and ":" in item_id
        else None
    )
    if parsed:
        return parsed
    try:
        return table, int(item_id)
    except (TypeError, ValueError, OverflowError):
        return _refuse("`show` needs `item_id` (a number, or `<table>:<id>`)")


def _show(package: str, table: str, item_id: Any) -> str:
    got = _show_id(table, item_id)
    if isinstance(got, str):
        return got
    table, number = got
    if not number:
        return _refuse("`show` needs `item_id`")
    found, problem = _read_rows(package, table, kdb.Where("id = ?", (number,), 1))
    if problem:
        return _refuse(problem)
    if not found:
        return "No %s item #%d for `%s`." % (table, number, package)
    return wrap_untrusted(
        "app_knowledge_" + table,
        _render_row(table, found[0]),
        limit=knowledge_limits.STABLE_BLOCK_CHARS,
    )
