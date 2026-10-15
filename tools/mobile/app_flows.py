"""Saved flows: a short reusable UI script an app remembers (``flows.json``).

A flow is a TEMPLATE, never a recording of values. A typed value is either a
``{placeholder}`` filled at replay from the call's ``flow_params``, or a secret step
(``secret: true`` plus a ``field``) whose value is read at replay from the tester's inputs,
exactly as an ordinary script's secret step is. Element ids are stripped on save (they are
positional per dump); only selectors that survive a renumber are kept.

A flow is replayed as ONE ordinary step script through ``session.submit`` and
``executor.replay``, so the destructive guard, the budgets and the ``missing_element`` stop
all apply unchanged. This module adds no way to act on the device and never calls a model.
Every public function returns an error/content dict and never raises.
"""

from __future__ import annotations

import copy
import json
import logging
from typing import Any

from tools.mobile import app_flows_meta, app_store
from tools.mobile.app_flows_check import secret_fields, template_from_script
from tools.mobile.app_flows_consts import (
    ALLOWED_OPS,
    MAX_FIELD_CHARS,
    MAX_FLOW_NAME_CHARS,
    MAX_PARAM_NAME_CHARS,
    MAX_PARAMS_PER_FLOW,
    OK_OUTCOMES,
    valid_name,
)
from tools.mobile.app_flows_consts import bad_name as _bad_name
from tools.mobile.app_flows_consts import err as _err
from tools.mobile.app_flows_expand import expand
from tools.mobile.app_flows_replay import clean_replay, stop_line

__all__ = [
    "ALLOWED_OPS",
    "FLOWS_FILE",
    "MAX_FIELD_CHARS",
    "MAX_FLOWS_PER_APP",
    "MAX_FLOW_NAME_CHARS",
    "MAX_PARAMS_PER_FLOW",
    "MAX_PARAM_NAME_CHARS",
    "OK_OUTCOMES",
    "check_save",
    "clean_replay",
    "delete_flow",
    "expand",
    "get_flow",
    "list_flows",
    "save_flow",
    "secret_fields",
    "stop_line",
    "template_from_script",
    "valid_name",
]

logger = logging.getLogger(__name__)

FLOWS_FILE = "flows.json"

#: Flows one app may keep. A NEW name past it is refused; overwriting one is not.
MAX_FLOWS_PER_APP = 20


def _add_flow(body: dict, name: str, template: dict) -> str:
    """Put *template* into *body* under *name*; ``''`` or the count-cap refusal."""
    flows = body.get("flows")
    if not isinstance(flows, dict):
        flows = body["flows"] = {}
    if name not in flows and len(flows) >= MAX_FLOWS_PER_APP:
        return (
            "this app already has %d saved flows; delete one first" % MAX_FLOWS_PER_APP
        )
    entry = {"params": list(template["params"]), "steps": list(template["steps"])}
    meta = app_flows_meta.clean(template.get("meta"), len(entry["steps"]))
    flows[name] = dict(entry, meta=meta) if meta else entry
    return ""


def check_save(package: Any, name: Any, template: dict) -> str:
    """Why saving *template* as *name* would be refused, or ``''``: run BEFORE the replay,
    so a flow that cannot be stored never drives the device. ``save_flow`` re-checks."""
    try:
        if not valid_name(name):
            return _bad_name()["error"]
        body = copy.deepcopy(app_store.read_json(package, FLOWS_FILE))
        problem = _add_flow(body, str(name), template)
        if problem:
            return problem
        size = len(json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        if size > app_store.MAX_FILE_BYTES:
            return (
                "this flow would push the saved flows past %d bytes; delete one first"
                % (app_store.MAX_FILE_BYTES)
            )
        return ""
    except Exception:
        logger.exception("mobile.app_flows.check_save failed")
        return "the flow store is unavailable"


def save_flow(package: Any, name: Any, template: dict) -> dict:
    if not valid_name(name):
        return _bad_name()
    return app_store.update_json(
        package, FLOWS_FILE, lambda body: _add_flow(body, str(name), template)
    )


def get_flow(package: Any, name: Any) -> dict:
    """The stored template, re-validated: the file is user-editable, so it is never trusted."""
    if not valid_name(name):
        return _bad_name()
    flows = app_store.read_json(package, FLOWS_FILE).get("flows")
    entry = flows.get(name) if isinstance(flows, dict) else None
    if not isinstance(entry, dict):
        return _err("no saved flow with that name")
    return _with_meta(template_from_script(entry.get("steps")), entry)


def _with_meta(got: dict, entry: dict) -> dict:
    """*got* (a parsed template) carrying the entry's per-step screen record."""
    if not got.get("error"):
        meta = app_flows_meta.clean(entry.get("meta"), len(got["content"]["steps"]))
        got["content"] = dict(got["content"], meta=meta) if meta else got["content"]
    return got


def list_flows(package: Any) -> dict:
    flows = app_store.read_json(package, FLOWS_FILE).get("flows")
    rows: list = []
    for name in sorted(flows if isinstance(flows, dict) else ()):
        got = get_flow(package, name)
        if got.get("error"):
            continue
        template = got["content"]
        rows.append(
            {
                "name": name,
                "steps": len(template["steps"]),
                "params": template["params"],
                "fields": secret_fields(template),
                "reached": app_flows_meta.reached(template),
                "covers": app_flows_meta.covers(template),
            }
        )
    return {"error": None, "content": rows}


def delete_flow(package: Any, name: Any) -> dict:
    if not valid_name(name):
        return _bad_name()

    def mutate(body: dict) -> str:
        flows = body.get("flows")
        if isinstance(flows, dict) and name in flows:
            del flows[name]
            return ""
        return "no saved flow with that name"

    return app_store.update_json(package, FLOWS_FILE, mutate)
