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
import re

from tools.mobile import actions, app_knowledge, app_store

logger = logging.getLogger(__name__)

FLOWS_FILE = "flows.json"

#: Flows one app may keep. A NEW name past it is refused; overwriting one is not.
MAX_FLOWS_PER_APP = 20
#: Characters of a flow name.
MAX_FLOW_NAME_CHARS = 40
#: Distinct {placeholder} parameters one flow may declare.
MAX_PARAMS_PER_FLOW = 8
#: Characters of a parameter name.
MAX_PARAM_NAME_CHARS = 32
#: Characters of a secret step's field name (the action schema itself allows 80).
MAX_FIELD_CHARS = 80

ALLOWED_OPS = (
    "tap",
    "tap_text",
    "type",
    "fill",
    "clear",
    "back",
    "press",
    "scroll",
    *actions.WAIT_UNTIL_OPS,
)

_REFUSED_WHY = {
    "clear_app_data": "it wipes the app",
    "open_url": "it leaves the app",
    "ask_tester": "a flow never stops to ask mid-replay",
    "done": "only the model ends a case",
    "wait": "wait for something instead (wait_until_text, _gone, _changed or _idle)",
}

#: Trace outcomes of a step that ran as planned (executor.py mints both; a secret
#: `type` step records 'ok'; 'supplied' belongs to ask_tester, which a flow refuses).
OK_OUTCOMES = frozenset(("ok", "assert_pass"))

_NAME_RE = re.compile(r"[a-z][a-z0-9_-]{0,%d}" % (MAX_FLOW_NAME_CHARS - 1))
_PLACEHOLDER_RE = re.compile(
    r"[{]([a-z][a-z0-9_]{0,%d})[}]" % (MAX_PARAM_NAME_CHARS - 1)
)
_CREDENTIAL_WORDS = frozenset(
    (
        "pass",
        "password",
        "passwd",
        "passcode",
        "pwd",
        "secret",
        "token",
        "otp",
        "pin",
        "code",
        "card",
        "cvv",
        "key",
        "ssn",
        "auth",
    )
)


def _err(message: str) -> dict:
    return {"error": message, "content": None}


def valid_name(name: object) -> bool:
    return isinstance(name, str) and bool(_NAME_RE.fullmatch(name))


def _bad_name() -> dict:
    return _err(
        "not a valid flow name (lower-case letters, digits, _ and -, at most %d characters)"
        % MAX_FLOW_NAME_CHARS
    )


def _check_typed(index: int, step: dict, params: list) -> str:
    text = str(step.get("text") or "")
    field = str(step.get("field") or "")
    if step.get("secret"):
        if not field.strip() or len(field) > MAX_FIELD_CHARS:
            return (
                "step %d: a secret step needs a `field` name of at most %d characters"
                % (
                    index,
                    MAX_FIELD_CHARS,
                )
            )
        why = app_knowledge.secret_reason(field)
        if why:
            return "step %d is not saved: its `field` name is refused: %s" % (
                index,
                why,
            )
        if text:
            return (
                "step %d: a secret step carries no `text`; its value comes from the tester at replay"
                % index
            )
        return ""
    found = _PLACEHOLDER_RE.fullmatch(text)
    if not found:
        return (
            "step %d: typed text must be one {placeholder}, or a secret step with a `field`; "
            "a flow never stores a typed value" % index
        )
    name = found.group(1)
    if any(word in _CREDENTIAL_WORDS for word in name.split("_")):
        return (
            "step %d: parameter `%s` is named like a credential; use a secret step with a `field`"
            % (
                index,
                name,
            )
        )
    if name not in params:
        params.append(name)
    return ""


def _screen_strings(step: dict) -> list:
    out: list = []
    target = step.get("target")
    if isinstance(target, dict):
        out += [target.get(key) for key in ("text", "label", "rid")]
    if step.get("op") == "fill":
        out.append(step.get("label"))
    elif step.get("op") != "type":
        out.append(step.get("text"))
    return [str(value) for value in out if value]


def _check_step(index: int, step: object, params: list) -> tuple[dict, str]:
    """``(clean_step, "")`` for a replayable step, else ``({}, problem)``."""
    if not isinstance(step, dict):
        return {}, "step %d is not an object" % index
    op = str(step.get("op") or "")
    if op not in ALLOWED_OPS:
        why = _REFUSED_WHY.get(op, "it is not a replayable UI action")
        return {}, "step %d: `%s` cannot be saved in a flow (%s)" % (
            index,
            op[:30],
            why,
        )
    clean = copy.deepcopy(step)
    target = clean.get("target")
    if isinstance(target, dict):
        target.pop("id", None)
        if not any(
            str(target.get(key) or "").strip()
            for key in ("text", "rid", "role", "label")
        ):
            return {}, (
                "step %d: its target is a positional id only, which does not survive a replay"
                % index
            )
    if op in ("type", "fill"):
        problem = _check_typed(index, clean, params)
        if problem:
            return {}, problem
    for value in _screen_strings(clean):
        why = app_knowledge.secret_reason(value)
        if why:
            return {}, "step %d is not saved: %s" % (index, why)
    return clean, ""


def _template_payload(raw: object) -> tuple:
    """The script's action list and no problem, or None and the refusal text."""
    payload = raw
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            return None, "the script is not valid JSON"
    if isinstance(payload, dict):
        payload = payload.get("actions")
    if not isinstance(payload, list) or not payload:
        return None, "a flow needs a non-empty `actions` list"
    if len(payload) > actions.MAX_MODEL_ACTIONS:
        return None, "a flow holds at most %d steps" % actions.MAX_MODEL_ACTIONS
    return payload, None


def template_from_script(raw: object) -> dict:
    """A validated template ``{'params', 'steps'}`` from a script (JSON text, list or dict).

    Refuses an op that cannot be replayed blind, a literal typed value, a target that is only
    a positional id, a credential-looking parameter name, and any screen string the note
    scrub would refuse. The caller's data is never mutated.
    """
    try:
        payload, problem = _template_payload(raw)
        if problem:
            return _err(problem)
        steps: list = []
        params: list = []
        for index, step in enumerate(payload, 1):
            clean, problem = _check_step(index, step, params)
            if problem:
                return _err(problem)
            steps.append(clean)
        if len(params) > MAX_PARAMS_PER_FLOW:
            return _err("a flow takes at most %d parameters" % MAX_PARAMS_PER_FLOW)
        parsed = actions.parse_script({"actions": steps})
        if parsed.get("error"):
            return _err(str(parsed["error"])[:300])
        return {"error": None, "content": {"params": params, "steps": steps}}
    except Exception:
        logger.exception("mobile.app_flows.template_from_script failed")
        return _err("the flow could not be checked")


def secret_fields(template: dict) -> list:
    return sorted(
        {str(s.get("field")) for s in template.get("steps") or [] if s.get("secret")}
    )


def expand(template: dict, params: object, supplied: object) -> dict:
    """The script JSON to replay: placeholders filled, then parsed again as the final gate.

    Refuses BEFORE any device work when a parameter is missing or unknown, or when a secret
    step's field has no value supplied. *supplied* is a collection of field NAMES only.
    """
    try:
        if params is None or params == "":
            params = {}
        if not isinstance(params, dict):
            return _err("`flow_params` must be a JSON object")
        declared = list(template.get("params") or [])
        missing = [p for p in declared if p not in params]
        extra = sorted(str(k)[:40] for k in params if k not in declared)
        if missing or extra:
            return _err(
                "flow parameters do not match: missing %s, unknown %s"
                % (missing or "none", extra or "none")
            )
        for key, value in params.items():
            if not isinstance(value, str) or len(value) > actions.MAX_TEXT_CHARS:
                return _err(
                    "parameter `%s` must be text of at most %d characters"
                    % (str(key)[:40], actions.MAX_TEXT_CHARS)
                )
        absent = [f for f in secret_fields(template) if f not in set(supplied or ())]
        if absent:
            return _err(
                "the tester has not supplied a value for secret field(s) %s" % absent
            )
        steps = copy.deepcopy(template.get("steps") or [])
        for step in steps:
            if step.get("op") in ("type", "fill") and not step.get("secret"):
                found = _PLACEHOLDER_RE.fullmatch(str(step.get("text") or ""))
                step["text"] = params[found.group(1)] if found else ""
        parsed = actions.parse_script({"actions": steps})
        if parsed.get("error"):
            return _err(str(parsed["error"])[:300])
        return {"error": None, "content": json.dumps({"actions": steps})}
    except Exception:
        logger.exception("mobile.app_flows.expand failed")
        return _err("the flow could not be expanded")


def _add_flow(body: dict, name: str, template: dict) -> str:
    """Put *template* into *body* under *name*; ``''`` or the count-cap refusal."""
    flows = body.get("flows")
    if not isinstance(flows, dict):
        flows = body["flows"] = {}
    if name not in flows and len(flows) >= MAX_FLOWS_PER_APP:
        return (
            "this app already has %d saved flows; delete one first" % MAX_FLOWS_PER_APP
        )
    flows[name] = {"params": list(template["params"]), "steps": list(template["steps"])}
    return ""


def check_save(package: object, name: object, template: dict) -> str:
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


def save_flow(package: object, name: object, template: dict) -> dict:
    if not valid_name(name):
        return _bad_name()
    return app_store.update_json(
        package, FLOWS_FILE, lambda body: _add_flow(body, str(name), template)
    )


def get_flow(package: object, name: object) -> dict:
    """The stored template, re-validated: the file is user-editable, so it is never trusted."""
    if not valid_name(name):
        return _bad_name()
    flows = app_store.read_json(package, FLOWS_FILE).get("flows")
    entry = flows.get(name) if isinstance(flows, dict) else None
    if not isinstance(entry, dict):
        return _err("no saved flow with that name")
    return template_from_script(entry.get("steps"))


def list_flows(package: object) -> dict:
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
            }
        )
    return {"error": None, "content": rows}


def delete_flow(package: object, name: object) -> dict:
    if not valid_name(name):
        return _bad_name()

    def mutate(body: dict) -> str:
        flows = body.get("flows")
        if isinstance(flows, dict) and name in flows:
            del flows[name]
            return ""
        return "no saved flow with that name"

    return app_store.update_json(package, FLOWS_FILE, mutate)


def clean_replay(case: object, count: int) -> bool:
    """True when the LAST *count* trace entries all ran as planned.

    The trace is merged across submits, so only the tail belongs to this replay.
    """
    trace = case.get("trace") if isinstance(case, dict) else None
    if not isinstance(trace, list) or count < 1 or len(trace) < count:
        return False
    return all(
        isinstance(entry, dict) and str(entry.get("outcome") or "") in OK_OUTCOMES
        for entry in trace[-count:]
    )


def stop_line(name: str, case: object, count: int) -> str:
    """The sentence for a replay that did not finish as saved. Never says a step was skipped."""
    trace = case.get("trace") if isinstance(case, dict) else None
    tail = list(trace[-count:]) if isinstance(trace, list) and count > 0 else []
    step = len(tail) + 1
    for index, entry in enumerate(tail, 1):
        if not (
            isinstance(entry, dict) and str(entry.get("outcome") or "") in OK_OUTCOMES
        ):
            step = index
            break
    return (
        "Flow `%s` stopped at step %d of %d: the screen no longer matches the saved steps. "
        "Nothing was tapped blind; carry on from the packet below, and save the flow again "
        "if the app changed." % (name, min(step, count), count)
    )
