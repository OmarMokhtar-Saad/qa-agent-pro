"""``qa_mobile_knowledge``: read and manage ONE app's saved knowledge (contract 1.10).

Sync, called from ``mcp_handlers.handle_mobile_knowledge`` inside ``to_thread``
with the package already resolved. Returns ``str`` and never raises. Wave 1
implements ``list`` and ``show`` for every table; the other actions reply "not
implemented yet" until W2d fills them. Stored text is only echoed through
``wrap_untrusted``; ``reason``, ``text`` and ``payload`` are refused when they
hold a secret.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from contextlib import closing

from tools.mobile import (
    app_knowledge,
    app_store,
    knowledge_limits,
    knowledge_reflect,
    knowledge_schema,
    run_store,
)
from tools.mobile import knowledge_db as kdb
from tools.mobile import knowledge_gate as gate
from tools.mobile import knowledge_plug_locators as plug_locators
from tools.mobile import knowledge_stage_popups as popups_stage
from tools.untrusted import single_line, wrap_untrusted

logger = logging.getLogger(__name__)

ACTIONS = (
    "list",
    "show",
    "confirm",
    "reject",
    "edit",
    "resolve",
    "export",
    "import",
    "rollback",
    "reflect",
    "reflect_submit",
    "review",
)
TABLES = (
    "notes",
    "lessons",
    "shortcuts",
    "elements",
    "screens",
    "edges",
    "timings",
    "popups",
    "mistakes",
)
_WARN = "⚠️ "
_ROW_CHARS = 600


def _refuse(message: str) -> str:
    return _WARN + message


def notes_list(package: str) -> str:
    """The slice-1 ``qa_mobile_notes list`` reply, byte for byte."""
    listed = app_knowledge.list_notes(package)
    if listed.get("error"):
        return _WARN + single_line(listed["error"], 300)
    return app_knowledge.render(package, listed["content"])


def notes_retire(package: str, note_id: object, reason: str, run_id: str) -> str:
    """The slice-1 ``qa_mobile_notes retire`` reply, byte for byte."""
    done = app_knowledge.retire_note(
        package, note_id, reason=str(reason or ""), run_id=run_id
    )
    if done.get("error"):
        return _WARN + single_line(done["error"], 300)
    return "Retired app note #%s for `%s`. It stays in history." % (note_id, package)


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


def _read_rows(package: str, table: str, where: str, args: tuple, limit: int):
    path = app_knowledge.db_path(package)
    if path is None:
        return None, "`package` is not an Android package name"
    if not path.exists():
        return [], ""
    with closing(app_knowledge._connect(path, write=False)) as conn:
        return kdb.rows(conn, table, where, args, limit), ""


def _render_row(table: str, row: dict) -> str:
    skip = {"id"}
    body = ", ".join(
        "%s=%s" % (k, single_line(v, 120))
        for k, v in row.items()
        if k not in skip and v not in (None, "")
    )
    return "%s:%s %s" % (table, row.get("id"), body[:_ROW_CHARS])


def _list(package: str, table: str, page: int) -> str:
    if table == "notes":
        return notes_list(package)
    size = knowledge_limits.LIST_PAGE_ROWS
    start = max(0, int(page or 0)) * size
    found, problem = _read_rows(package, table, "", (), start + size + 1)
    if problem:
        return _refuse(problem)
    window = (found or [])[start : start + size]
    if not window:
        return "No %s saved for `%s` yet." % (table, package)
    more = len(found) > start + size
    body = "\n".join(_render_row(table, r) for r in window)
    out = "%s for `%s` (page %d):\n%s" % (
        table,
        package,
        max(0, int(page or 0)),
        wrap_untrusted(
            "app_knowledge_" + table, body, limit=knowledge_limits.STABLE_BLOCK_CHARS
        ),
    )
    return out + ("\nMore: pass page=%d." % (int(page or 0) + 1) if more else "")


def _show(package: str, table: str, item_id: object) -> str:
    parsed = (
        kdb.parse_item_id(item_id)
        if isinstance(item_id, str) and ":" in item_id
        else None
    )
    if parsed:
        table, number = parsed
    else:
        try:
            number = int(item_id)
        except (TypeError, ValueError, OverflowError):
            return _refuse("`show` needs `item_id` (a number, or `<table>:<id>`)")
    if not number:
        return _refuse("`show` needs `item_id`")
    found, problem = _read_rows(package, table, "id = ?", (number,), 1)
    if problem:
        return _refuse(problem)
    if not found:
        return "No %s item #%d for `%s`." % (table, number, package)
    return wrap_untrusted(
        "app_knowledge_" + table,
        _render_row(table, found[0]),
        limit=knowledge_limits.STABLE_BLOCK_CHARS,
    )


# --- write actions (W2d) ------------------------------------------------------

_SQL_ERRORS = (sqlite3.Error, ValueError, TypeError, KeyError, AttributeError, OSError)
_EDITABLE = {"notes": "text", "lessons": "text", "mistakes": "fix"}


def _target(table: str, item_id: object):
    """``(table, number)`` from ``<table>:<id>`` or a bare number, else ``None``."""
    if isinstance(item_id, str) and ":" in item_id:
        return kdb.parse_item_id(item_id)
    try:
        number = int(item_id)
    except (TypeError, ValueError, OverflowError):
        return None
    return (table, number) if number > 0 and table in TABLES else None


def _write(package: str, work) -> str:
    """Run ``work(conn)`` in one immediate transaction; its string is the reply."""
    conn = kdb.open_rw(package)
    if conn is None:
        return _refuse(
            "`package` is not an Android package name or the store is unavailable"
        )
    try:
        conn.execute("BEGIN IMMEDIATE")
        reply = work(conn)
        conn.commit()
        return reply
    except _SQL_ERRORS:
        logger.exception("qa_mobile_knowledge write failed")
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        return _refuse("the knowledge store could not be changed")
    finally:
        conn.close()


def _row_of(conn, table: str, number: int):
    return conn.execute(
        "SELECT * FROM %s WHERE id = ? AND invalid_at IS NULL" % table, (number,)
    ).fetchone()


def _said(text: str) -> str:
    return wrap_untrusted("app_knowledge_reply", text, limit=2000)


def _confirm(package: str, tgt, run_id: str) -> str:
    def work(conn):
        row = _row_of(conn, *tgt)
        if row is None:
            return _refuse("no open item %s:%s" % tgt)
        status = (
            "active"
            if row["status"] in ("candidate", "needs_recheck", "stale")
            else row["status"]
        )
        kdb.set_status(
            conn,
            tgt[0],
            tgt[1],
            status,
            run_id=run_id,
            why="confirmed by the tester",
            confirmed=int(row["confirmed"] or 0) + 1,
            runs_json=kdb._bump_runs(row["runs_json"], run_id),
        )
        return _said("%s:%s is %s (confirmed)" % (tgt[0], tgt[1], status))

    return _write(package, work)


def _reject(package: str, tgt, reason: str, run_id: str) -> str:
    def work(conn):
        if _row_of(conn, *tgt) is None:
            return _refuse("no open item %s:%s" % tgt)
        why = single_line(reason, 200)
        if app_knowledge.secret_reason(why, (), run_id):
            why = ""
        kdb.set_status(
            conn, tgt[0], tgt[1], "retired", run_id=run_id, why=why or "rejected"
        )
        return _said("%s:%s retired" % tgt)

    return _write(package, work)


def _clone(row, column: str, text: str) -> dict:
    values = {k: row[k] for k in row.keys() if k not in gate._KEEP_OUT}
    values[column] = text
    return values


def _edit(package: str, tgt, text: str, run_id: str) -> str:
    table, number = tgt
    column = _EDITABLE.get(table)
    if column is None or not str(text or "").strip():
        return _refuse("`edit` needs `text`, and works on notes, lessons and mistakes")

    def work(conn):
        row = _row_of(conn, table, number)
        if row is None:
            return _refuse("no open item %s:%s" % tgt)
        clean = " ".join(str(text).split())
        if table == "notes":
            got = app_knowledge._validate(
                clean,
                row["kind"],
                json.loads(row["when_json"] or "{}"),
                json.loads(row["then_json"] or "{}"),
            )
            if isinstance(got, str):
                return _refuse(got)
            clean = got[0]
        values = {
            **_clone(row, column, clean),
            "trust": "tester",
            "status": "active",
            "source_run": run_id,
            "runs_json": "[]",
        }
        new = kdb.supersede(
            conn, table, number, values, run_id=run_id, why="edited by the tester"
        )
        return (
            _said("%s:%s replaced by %s:%s" % (table, number, table, new))
            if new
            else _refuse("the edit was refused")
        )

    return _write(package, work)


def _resolve(package: str, tgt, verdict: str, run_id: str) -> str:
    verdict = str(verdict or "").lower()
    if tgt[0] != "notes" or verdict not in ("keep", "replace", "narrow", "delete"):
        return _refuse(
            "`resolve` needs a notes item and `verdict` keep, replace, narrow or delete"
        )

    def work(conn):
        row = _row_of(conn, "notes", tgt[1])
        if row is None or not (row["proposal_json"] or row["status"] == "disputed"):
            return _refuse("note #%s is not disputed" % tgt[1])
        prop = json.loads(row["proposal_json"] or "{}")
        why = "resolved: " + verdict
        if verdict == "delete":
            kdb.set_status(
                conn,
                "notes",
                tgt[1],
                "retired",
                run_id=run_id,
                why=why,
                proposal_json="",
            )
        elif verdict == "keep":
            kdb.set_status(
                conn,
                "notes",
                tgt[1],
                "active",
                run_id=run_id,
                why=why,
                proposal_json="",
            )
        else:
            problem = _apply_proposal(conn, row, verdict, prop, run_id)
            if problem:
                return _refuse(problem)
        return _said("note #%s resolved: %s" % (tgt[1], verdict))

    return _write(package, work)


def _apply_proposal(conn, row, verdict: str, prop: dict, run_id: str) -> str:
    """``""`` on success, else why a replace/narrow could not be made."""
    text = " ".join(str(prop.get("proposed") or "").split())
    if not text:
        return "the dispute holds no proposal"
    got = app_knowledge._validate(
        text,
        row["kind"],
        json.loads(row["when_json"] or "{}"),
        json.loads(row["then_json"] or "{}"),
    )
    if isinstance(got, str):
        return got
    if verdict == "narrow":
        env = prop.get("env") or {}
        if not env:
            return "the dispute names no context to narrow to"
        values = gate._variant_values("notes", row, got[0], env)
        values.update(
            trust="tester", status="active", source_run=run_id, runs_json="[]"
        )
        kdb.set_status(
            conn,
            "notes",
            row["id"],
            "active",
            run_id=run_id,
            why="resolved: narrow",
            proposal_json="",
        )
        return (
            ""
            if kdb.insert(conn, "notes", values, run_id=run_id, why="resolved: narrow")
            else "the variant was refused"
        )
    values = {
        **_clone(row, "text", got[0]),
        "trust": "tester",
        "status": "active",
        "source_run": run_id,
        "runs_json": "[]",
        "proposal_json": "",
    }
    done = kdb.supersede(
        conn, "notes", row["id"], values, run_id=run_id, why="resolved: replace"
    )
    return "" if done else "the replacement was refused"


_EXPORT_STATUS = "status IN ('active', 'candidate') AND invalid_at IS NULL"


def _export(package: str) -> str:
    path = app_knowledge.db_path(package)
    if path is None or not path.exists():
        return _refuse("no knowledge saved for `%s` yet" % package)
    tables: dict = {}
    left = knowledge_limits.EXPORT_MAX_ROWS
    with closing(app_knowledge._connect(path, write=False)) as conn:
        for name in TABLES:
            found = (
                kdb.rows(
                    conn,
                    name,
                    _EXPORT_STATUS,
                    (),
                    min(left, knowledge_limits.SNAPSHOT_MAX_ROWS),
                )
                if left > 0
                else []
            )
            tables[name] = found
            left -= len(found)
    body = {"package": package, "tables": tables}
    saved = app_store.write_json(package, "export.json", body)
    note = (
        ""
        if not saved.get("error")
        else "\n(not written to export.json: %s)" % single_line(saved["error"], 100)
    )
    text = json.dumps(body, ensure_ascii=False, sort_keys=True, default=str)
    return (
        wrap_untrusted(
            "app_knowledge_export", text, limit=knowledge_limits.PAYLOAD_CHARS
        )
        + note
    )


_SKIP_IMPORT = set(gate._KEEP_OUT) | {"_table", "recheck_at", "created"}
#: Evidence an imported row must earn again on this install. Every numeric
#: column with a schema default (counters, timings, retention) is dropped so the
#: default applies; measured samples and locally observed element state restart
#: empty. One local run therefore cannot activate or outrank an import.
_EVIDENCE_NUMERIC = frozenset(
    re.findall(
        r"(\w+) (?:INTEGER|REAL) DEFAULT",
        "".join(knowledge_schema._TABLES.values()) + knowledge_schema._COMMON,
    )
)
_EVIDENCE = {
    "samples_json": "[]",
    "bound_field": "",
    "missing_at": "",
    "last_used_run": "",
}
#: The only tables an import may write. The rest are learned on this install
#: alone: a shortcut or route seed activates after one replay, an imported
#: mistake can be credited to tester trust by one local correction, and an
#: imported edge never promotes yet shadows the local one.
_IMPORTABLE = frozenset({"notes", "lessons", "timings", "popups", "elements"})
#: Open imported rows may take at most this share of a table's row cap, so
#: imports can never leave local learning refused as "cap: full".
_IMPORT_SHARE = 0.5
#: table -> {json column: the shape its schema default has (dict or list)}.
_JSON_SHAPES = {
    table: {
        col: dict if default == "{}" else list
        for col, default in re.findall(
            r"(\w+_json) TEXT DEFAULT '(\{\}|\[\])'", cols + knowledge_schema._COMMON
        )
    }
    for table, cols in knowledge_schema._TABLES.items()
}


def _strict_dumps(value: object) -> str:
    """Strict JSON: NaN/Infinity raise ValueError (a validation error)."""
    return json.dumps(value, sort_keys=True, allow_nan=False)


def _popup_consistent(values: dict, dismiss: object) -> bool:
    """The dismiss must be a tap on a named rid that the signature agrees with:
    its activity is the signature's prefix and, when the signature carries no
    anchors, the signature's ``dismiss:<rid>`` basis is that very rid."""
    if not isinstance(dismiss, dict) or dismiss.get("op", "tap") != "tap":
        return False
    rid, sig = dismiss.get("rid"), values.get("signature")
    if not isinstance(rid, str) or not rid or not isinstance(sig, str):
        return False
    activity, bar, basis = sig.partition("|")
    if not bar or str(dismiss.get("activity") or "") != activity:
        return False
    if basis.startswith("dismiss:"):
        return sig == popups_stage.signature(activity, [], rid)
    return bool(basis)


def _import_note(values: dict) -> bool:
    # One malformed row is skipped, never aborts the whole import.
    try:
        when = json.loads(values.get("when_json") or "{}")
        then = json.loads(values.get("then_json") or "{}")
    except (TypeError, ValueError):
        return False
    if not isinstance(when, dict) or not isinstance(then, dict):
        return False
    got = app_knowledge._validate(values.get("text"), values.get("kind"), when, then)
    if isinstance(got, str):
        return False
    try:
        values.update(
            text=got[0],
            when_json=_strict_dumps(got[1]),
            then_json=_strict_dumps(got[2]),
        )
    except ValueError:
        return False
    values["scope_key"] = "%s|%s" % (values["kind"], values["when_json"])
    return True


def _import_learned(table: str, values: dict) -> bool:
    """Each JSON column must parse to its schema shape; a destructive-looking
    popup dismiss is refused, as the popups stage never records one."""
    for col, shape in _JSON_SHAPES.get(table, {}).items():
        if col not in values or col == "runs_json":
            continue
        raw = values[col]
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(parsed, shape):
                return False
            values[col] = _strict_dumps(parsed)
        except ValueError:
            return False
    if table == "popups":
        dismiss = json.loads(values.get("dismiss_json") or "{}")
        if not _popup_consistent(values, dismiss):
            return False
        if popups_stage.looks_destructive(dismiss.get("rid")):
            return False
    if table == "elements" and "locators_json" in values:
        # Per-locator hit counts are evidence too; only the locators carry over.
        queue = json.loads(values["locators_json"])
        values["locators_json"] = _strict_dumps(
            [
                {"l": e["l"], "h": 0, "m": 0}
                for e in queue
                if isinstance(e, dict)
                and isinstance(e.get("l"), str)
                and plug_locators.split_locator(e["l"])[0]
            ]
        )
    values.update({col: v for col, v in _EVIDENCE.items() if col in values})
    for col in _EVIDENCE_NUMERIC:
        values.pop(col, None)
    return True


def _import_room(conn, table: str) -> bool:
    cap = kdb._cap(table)
    if not cap:
        return True
    (held,) = conn.execute(
        "SELECT COUNT(*) FROM %s WHERE trust = 'imported' AND invalid_at IS NULL"
        % table
    ).fetchone()
    return held < int(cap * _IMPORT_SHARE)


def _import_row(conn, table: str, raw: dict, run_id: str) -> bool:
    if table not in _IMPORTABLE or not _import_room(conn, table):
        return False
    values = {
        k: v for k, v in raw.items() if k not in _SKIP_IMPORT and isinstance(k, str)
    }
    values.update(
        trust="imported", status="candidate", source_run="import", runs_json="[]"
    )
    ok = _import_note(values) if table == "notes" else _import_learned(table, values)
    if not ok:
        return False
    return kdb.insert(conn, table, values, run_id=run_id, why="import") is not None


def _import(package: str, payload: str, run_id: str) -> str:
    try:
        body = json.loads(payload or "{}")
        tables = body.get("tables") if isinstance(body, dict) else None
        if not isinstance(tables, dict):
            raise ValueError
    except (ValueError, AttributeError):
        return _refuse('`payload` must be an export: {"tables": {<table>: [rows]}}')

    def work(conn):
        seen = kept = 0
        for name, listing in tables.items():
            if name not in TABLES or not isinstance(listing, list):
                continue
            for raw in listing:
                seen += 1
                if seen > knowledge_limits.IMPORT_MAX_ROWS:
                    return _said(
                        "stopped at %d rows; imported %d as candidates"
                        % (knowledge_limits.IMPORT_MAX_ROWS, kept)
                    )
                if isinstance(raw, dict) and _import_row(
                    conn, name, raw, run_id or "import"
                ):
                    kept += 1
        return _said(
            "imported %d of %d rows as candidates (never active)" % (kept, seen)
        )

    return _write(package, work)


def _rollback(package: str, run_id: str) -> str:
    if not run_id:
        return _refuse("`rollback` needs `run_id`")

    def work(conn):
        got = knowledge_schema.rollback_run(conn, run_id)
        return _said(
            "rolled back %s row change(s) of run %s" % (got.get("undone", 0), run_id)
        )

    return _write(package, work)


def _pending(package: str) -> list:
    path = app_knowledge.db_path(package)
    if path is None or not path.exists():
        return []
    with closing(app_knowledge._connect(path, write=False)) as conn:
        found = conn.execute(
            "SELECT run_id FROM runs WHERE reflect_state = 'pending' ORDER BY rowid DESC LIMIT ?",
            (knowledge_limits.REFLECT_PENDING_MAX,),
        ).fetchall()
    return [r[0] for r in found]


def _reflect(package: str, run_id: str) -> str:
    runs = [run_id] if run_id else _pending(package)
    packets = [
        p for p in (knowledge_reflect.build_packet(package, r) for r in runs[:3]) if p
    ]
    if not packets:
        return "No reflection is pending for `%s`." % package
    return json.dumps(
        {"kind": "knowledge_reflections", "packets": packets}, ensure_ascii=False
    )[: knowledge_limits.PAYLOAD_CHARS]


def _submit(package: str, run_id: str, payload: str) -> str:
    try:
        body = json.loads(payload or "{}")
    except ValueError:
        return _refuse("`payload` must be JSON")
    answers = body.get("answers") if isinstance(body, dict) else None
    if not isinstance(answers, list):
        answers = [body]
    out = []
    for answer in answers[:3]:
        rid = str(
            (answer.get("run_id") if isinstance(answer, dict) else "") or run_id or ""
        )
        got = (
            knowledge_reflect.apply_answer(package, rid, answer)
            if rid
            else {"content": _said("no run_id")}
        )
        out.append(got.get("content") or got.get("error") or "")
    return "\n".join(out)


def _review(package: str) -> str:
    from tools.mobile import knowledge_review_review

    lines = knowledge_review_review.review_lines(package)
    return wrap_untrusted(
        "app_knowledge_review",
        "\n".join(lines) or "Nothing to review.",
        limit=knowledge_limits.STABLE_BLOCK_CHARS,
    )


def _package_of(package: str, run_id: str) -> str:
    if package or not run_id:
        return package
    try:
        got = run_store.read_manifest(run_id).get("content") or {}
        return str(got.get("package") or "")
    except _SQL_ERRORS:
        return ""


def _act(verb: str, package: str, item_id: object, run_id: str, extra: dict) -> str:
    name = str(extra.get("table") or "notes").strip().lower()
    tgt = _target(name, item_id)
    payload = extra.get("payload") or ""
    payload = payload if isinstance(payload, str) else json.dumps(payload, default=str)
    untargeted = {
        "export": lambda: _export(package),
        "import": lambda: _import(package, payload, run_id),
        "rollback": lambda: _rollback(package, run_id),
        "reflect": lambda: _reflect(package, run_id),
        "reflect_submit": lambda: _submit(package, run_id, payload),
        "review": lambda: _review(package),
    }
    if verb in untargeted:
        return untargeted[verb]()
    if tgt is None:
        return _refuse("`%s` needs `item_id` (a number, or `<table>:<id>`)" % verb)
    if verb == "confirm":
        return _confirm(package, tgt, run_id)
    if verb == "reject":
        return _reject(package, tgt, str(extra.get("reason") or ""), run_id)
    if verb == "edit":
        return _edit(package, tgt, str(extra.get("text") or ""), run_id)
    return _resolve(package, tgt, str(extra.get("verdict") or ""), run_id)


def handle(
    action: str = "list",
    package: str = "",
    item_id: object = 0,
    run_id: str = "",
    data: dict | None = None,
) -> str:
    """``data`` carries the action fields: table, reason, text, verdict, payload, page."""
    try:
        extra = data if isinstance(data, dict) else {}
        verb = str(action or "list").strip().lower()
        if verb not in ACTIONS:
            return _refuse("`action` must be one of " + ", ".join(ACTIONS) + ".")
        name = str(extra.get("table") or "notes").strip().lower()
        if name not in TABLES:
            return _refuse("`table` must be one of " + ", ".join(TABLES) + ".")
        payload = extra.get("payload") or ""
        if not isinstance(payload, str):
            payload = json.dumps(payload, default=str)
        refusal = _screen_inputs(
            str(extra.get("reason") or ""), str(extra.get("text") or ""), payload
        )
        if refusal:
            return _refuse(refusal)
        package = _package_of(package, run_id)
        if verb == "list":
            return _list(package, name, extra.get("page") or 0)
        if verb == "show":
            return _show(package, name, item_id)
        return _act(verb, package, item_id, run_id, extra)
    except Exception:
        logger.exception("qa_mobile_knowledge failed")
        return _WARN + "The knowledge store could not be read."
