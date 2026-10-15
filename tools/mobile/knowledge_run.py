"""Run-level facts for per-app knowledge: env, app version, the learn marker.

``env`` is a new optional call parameter ``"name=value,name=value"`` (at most
``ENV_ENTRIES`` entries). It is stored ONCE in the run manifest
(``manifest["knowledge"]``); a later differing env is ignored. An undeclared env
is ``{}`` and an env-scoped note then does NOT apply (fail closed).

``begin_run`` writes a ``runs`` row in state ``unlearned`` at run start and
fires a background resume of earlier unlearned runs of the same package. Nothing
here raises and nothing blocks the executor's hot path.
"""

from __future__ import annotations

import asyncio
import json  # noqa: F401
import logging
import re  # noqa: F401
import time  # noqa: F401
from collections.abc import Callable
from dataclasses import dataclass, field  # noqa: F401

from tools.mobile import (
    app_info,
    app_knowledge,
    knowledge_db,  # noqa: F401
    knowledge_limits,  # noqa: F401
    run_store,
)
from tools.mobile.knowledge_run_args import (  # noqa: F401
    STATES,
    Move,
    RunFacts,
    RunKey,
)
from tools.mobile.knowledge_run_env import (  # noqa: F401
    _NAME_OK,
    MAX_ENV_VALUE,
    parse_env,
    parse_env_checked,
)
from tools.mobile.knowledge_run_state import (  # noqa: F401
    _finished,
    mark_state,
    pending_runs,
)
from tools.mobile.knowledge_run_store import (  # noqa: F401
    MAX_VERSIONS_SEEN,
    _begin_sync,
    _note_version,
    _store_version,
    _write_run_row,
    init_manifest,
)

logger = logging.getLogger(__name__)

# One implementation: notes and run facts must agree on what an env scope means.
env_matches = app_knowledge.env_matches

_FACTS: dict = {}
_MAX_FACTS = 64
_TASKS: set = set()


def _known(value: object) -> str:
    """A version read, with the literal ``null`` of a missing field as unknown."""
    text = str(value or "")
    return "" if text.strip().lower() in ("null", "none") else text


async def read_app_version(serial: str, package: str, *, shell=None) -> tuple[str, str]:
    """``(versionName, versionCode)`` of the installed app, ``("", "")`` on failure."""
    try:
        got = await app_info.read_version(serial, package, shell=shell)
        if got is None:
            return "", ""
        code = "" if got.version_code is None else str(got.version_code)
        return _known(got.version_name), _known(code)
    except Exception:
        logger.exception("knowledge: step failed")
        return "", ""


def manifest_knowledge(run_id: str) -> dict:
    try:
        body = (run_store.read_manifest(run_id) or {}).get("content") or {}
        know = body.get("knowledge")
        return know if isinstance(know, dict) else {}
    except Exception:
        logger.exception("knowledge: step failed")
        return {}


def _remember(facts: RunFacts) -> None:
    if len(_FACTS) >= _MAX_FACTS:
        _FACTS.pop(next(iter(_FACTS)))
    _FACTS[facts.run_id] = facts


async def _version(ctx: object, package: str, known: dict) -> tuple:
    name, code = _known(known.get("app_version")), _known(known.get("version_code"))
    if not name and package:
        serial = str(getattr(ctx, "serial", "") or "")
        name, code = await read_app_version(serial, package)
    return name, code


async def _read_facts(ctx: object, run_id: str, package: str) -> RunFacts:
    known = await asyncio.to_thread(manifest_knowledge, run_id) if run_id else {}
    env = getattr(ctx, "env", None) or known.get("env") or {}
    name, code = await _version(ctx, package, known)
    lane = str(getattr(ctx, "lane", "") or "")
    return RunFacts(run_id, package, str(name), str(code), dict(env), lane)


async def begin_run(ctx: object, resume: Callable | None = None) -> RunFacts:
    """Facts for this run, read ONCE (cached by run_id). Writes the ``runs`` row
    and fires the background ``resume`` (supplied by the caller) when given. Never raises, never blocks the start."""
    run_id = str(getattr(ctx, "run_id", "") or "")
    package = str(getattr(ctx, "package", "") or "")
    try:
        if run_id and run_id in _FACTS:
            return _FACTS[run_id]
        facts = await _read_facts(ctx, run_id, package)
        if run_id and package:
            _remember(facts)
            await asyncio.to_thread(_begin_sync, facts)
            if resume is not None:
                _fire_resume(resume, package, run_id)
        return facts
    except Exception:
        logger.exception("knowledge run: begin_run failed")
        return RunFacts(run_id, package)


def _fire_resume(resume: Callable, package: str, current: str) -> None:
    try:
        task = asyncio.get_running_loop().create_task(
            resume(package, exclude=(current,))
        )
        _TASKS.add(task)
        task.add_done_callback(_TASKS.discard)
    except Exception:
        logger.exception("knowledge run: resume not scheduled")
