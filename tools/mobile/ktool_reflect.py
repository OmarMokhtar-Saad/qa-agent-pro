"""``qa_mobile_knowledge`` reflection and review actions."""

from __future__ import annotations

import json
from contextlib import closing

from tools.mobile import (
    app_knowledge,
    knowledge_limits,
    knowledge_reflect,
    knowledge_review_review,
    run_store,
)
from tools.mobile.ktool_core import _SQL_ERRORS, _refuse, _said
from tools.untrusted import wrap_untrusted


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
