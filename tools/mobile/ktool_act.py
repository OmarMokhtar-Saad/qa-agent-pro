"""``qa_mobile_knowledge`` request intake and action dispatch."""

from __future__ import annotations

import json

from tools.mobile.ktool_args import Ask, Parsed, Scope
from tools.mobile.ktool_core import ACTIONS, TABLES, _refuse, _target
from tools.mobile.ktool_export import _export
from tools.mobile.ktool_import import _import
from tools.mobile.ktool_read import _screen_inputs
from tools.mobile.ktool_reflect import _package_of, _reflect, _review, _submit
from tools.mobile.ktool_resolve import _resolve
from tools.mobile.ktool_write import _confirm, _edit, _reject, _rollback


def _payload(extra: dict) -> str:
    payload = extra.get("payload") or ""
    return payload if isinstance(payload, str) else json.dumps(payload, default=str)


def _intake(ask: Ask):
    """The normalised request as ``Parsed``, or the refusal text."""
    extra = ask.data if isinstance(ask.data, dict) else {}
    verb = str(ask.action or "list").strip().lower()
    if verb not in ACTIONS:
        return _refuse("`action` must be one of " + ", ".join(ACTIONS) + ".")
    name = str(extra.get("table") or "notes").strip().lower()
    if name not in TABLES:
        return _refuse("`table` must be one of " + ", ".join(TABLES) + ".")
    refusal = _screen_inputs(
        str(extra.get("reason") or ""), str(extra.get("text") or ""), _payload(extra)
    )
    if refusal:
        return _refuse(refusal)
    package = _package_of(ask.package, ask.run_id)
    return Parsed(ask._replace(action=verb, package=package), name, extra)


def _untargeted(ask: Ask, payload: str) -> dict:
    package, run_id = ask.package, ask.run_id
    return {
        "export": lambda: _export(package),
        "import": lambda: _import(package, payload, run_id),
        "rollback": lambda: _rollback(package, run_id),
        "reflect": lambda: _reflect(package, run_id),
        "reflect_submit": lambda: _submit(package, run_id, payload),
        "review": lambda: _review(package),
    }


def _targeted(ask: Ask, tgt, extra: dict) -> str:
    verb, scope = ask.action, Scope(ask.package, ask.run_id)
    if verb == "confirm":
        return _confirm(ask.package, tgt, ask.run_id)
    if verb == "reject":
        return _reject(scope, tgt, str(extra.get("reason") or ""))
    if verb == "edit":
        return _edit(scope, tgt, str(extra.get("text") or ""))
    return _resolve(scope, tgt, str(extra.get("verdict") or ""))


def _act(ask: Ask, extra: dict) -> str:
    name = str(extra.get("table") or "notes").strip().lower()
    tgt = _target(name, ask.item_id)
    untargeted = _untargeted(ask, _payload(extra))
    if ask.action in untargeted:
        return untargeted[ask.action]()
    if tgt is None:
        return _refuse(
            "`%s` needs `item_id` (a number, or `<table>:<id>`)" % ask.action
        )
    return _targeted(ask, tgt, extra)
