"""Saved-app-note intake of the mobile step handlers: target, secrets, spec, save."""

from __future__ import annotations

import inspect
import json
import logging
from dataclasses import dataclass
from types import ModuleType
from typing import Callable

from handlers import _common
from tools import device_manager as _note_dm
from tools.mobile import app_knowledge, held_inputs, session_runs
from tools.untrusted import single_line

logger = logging.getLogger("tools.mcp_handlers")

_NOTE_NOT_JSON = (
    "Note not saved: pass `note` as a JSON object like "
    '{"kind": "wait", "text": "...", "when": {"rid": "send"}, '
    '"then": {"until_rid": "reply"}}.\n\n'
)
_NOTE_NO_TARGET = (
    "pass `package` (the app's Android package name, or `app` when it is one) "
    "or a `run_id` so it is filed under the right app"
)


def _note_run_package(rid: str) -> str:
    """The package a run's manifest names, or ``""`` (logged when it cannot resolve)."""
    try:
        found = session_runs.resolve(rid)
        return str(((found or {}).get("content") or {}).get("package") or "")
    except Exception:
        logger.exception("mobile note: could not resolve run %r", rid[:40])
    return ""


def _mobile_note_target(run_id: str = "", package: str = "") -> tuple:
    """``(package, run_id, refusal)`` for a saved-app-note call.

    A run id wins (its manifest names the app); otherwise a validated
    ``package``. Never raises.
    """
    rid = str(run_id or "").strip()
    pkg = _note_run_package(rid) if rid else ""
    if pkg:
        return pkg, rid, ""
    pkg = str(package or "").strip()
    if not pkg:
        return "", rid, _NOTE_NO_TARGET

    if _note_dm.valid_package_name(pkg):
        return pkg, rid, ""
    return "", rid, "`package` is not an Android package name"


def _mobile_call_secrets(
    tester_input: object = "", tester_inputs_json: object = ""
) -> list:
    """The values the tester supplied on THIS call, so a note that repeats one
    is refused. Never raises."""
    values: list = []
    if str(tester_input or "").strip():
        values.append(str(tester_input))
    try:
        raw = str(tester_inputs_json or "").strip()
        parsed = json.loads(raw) if raw else {}
        if isinstance(parsed, dict):
            values += [str(v) for v in parsed.values() if str(v).strip()]
    except ValueError:
        pass
    return values


def _mobile_held_values(run_id: object) -> list:
    """The values held for *run_id* right now. Never raises."""

    return [str(v) for v in held_inputs.recall(str(run_id or "")).values()]


def _call_arg(fn: Callable, call: tuple, name: str) -> str:
    """*name*'s value in a call to *fn*, however it was passed (bound by the
    real signature, so a reordered handler cannot misread it); ``""`` when it
    is absent or the call does not bind. Never raises."""
    args, kwargs = call
    if name in kwargs:
        return str(kwargs.get(name) or "")
    try:
        bound = inspect.signature(fn).bind_partial(*args, **kwargs)
    except (TypeError, ValueError):
        return ""
    return str(bound.arguments.get(name) or "")


def _mobile_note_spec(note: object) -> dict | None:
    """The ``note`` parameter as a dict, or ``None`` when it is not a JSON object."""
    try:
        spec = json.loads(note) if isinstance(note, str) else note
    except ValueError:
        return None
    return spec if isinstance(spec, dict) else None


def _note_saved_line(made: dict, pkg: str, echo: str) -> str:
    """The reply line for a saved note (what it does, how to list or retire it)."""
    line = "Saved app note #%s for `%s` (%s). " % (made["id"], pkg, made["kind"])
    scoped = "It is scoped to an environment and is saved, not applied yet: "
    scoped += "no step is changed by it today; "
    local = "It applies to this app's later steps on this machine; "
    line += scoped if (made.get("when") or {}).get("env") else local
    line += "`qa_mobile_notes` lists or retires it.\n" + echo
    if made.get("superseded"):
        line += "\nSuperseded: " + ", ".join("#%s" % n for n in made["superseded"])
    return line + "\n\n"


def _note_add(know: ModuleType, spec: dict, where: tuple) -> dict:
    """Store *spec* via *know*; *where* is ``(package, run_id, secrets)``."""
    pkg, rid, secrets = where
    draft = know.NoteDraft(
        spec.get("text", ""), spec.get("kind", ""), spec.get("when"), spec.get("then")
    )
    return know.add_note(pkg, draft, know.NoteMeta(source_run=rid, secrets=secrets))


def _note_save(note: object, where: tuple, secrets: object) -> str:
    """Parse, target and store the note; return the reply line. May raise.

    *where* is ``(run_id, package)``.
    """

    spec = _mobile_note_spec(note)
    if spec is None:
        return _NOTE_NOT_JSON
    pkg, rid, refusal = _mobile_note_target(*where)
    if refusal:
        return "Note not saved: " + refusal + ".\n\n"
    saved = _note_add(app_knowledge, spec, (pkg, rid, secrets))
    if saved.get("error"):
        return "Note not saved: " + single_line(saved["error"], 300) + "\n\n"
    made = saved["content"]
    return _note_saved_line(made, pkg, app_knowledge.echo(made))


@dataclass(frozen=True)
class NoteAsk:
    """One note to save: the note, the run or package it is filed under, secrets."""

    note: object = ""
    run_id: str = ""
    package: str = ""
    secrets: object = ()


def mobile_note_intake(ask: NoteAsk) -> str:
    """Save the ``note`` parameter and return a line to prefix the reply with
    (``""`` when there is no note, or the lane is off). Never raises."""
    if not str(ask.note or "").strip() or not _common.mobile_lane_enabled():
        return ""
    try:
        return _note_save(ask.note, (ask.run_id, ask.package), ask.secrets)
    except Exception:
        logger.exception("mobile note intake failed")
        return "Note not saved: internal error.\n\n"
