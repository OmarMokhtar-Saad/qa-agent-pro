"""Learning at the run's close (C11): ONE hook in ``run_finalize.finalize``.

Every close path (done, stop, idle_timeout, server_died, orphaned, released_idle) ends in
``finalize``, so learning fires once there. ``knowledge_run._fire_resume`` stays
only as a safety net for closed runs that were never learned.
"""

from __future__ import annotations

import asyncio
import logging
import threading

from tools.mobile import (
    knowledge_learn,
    knowledge_run_store,
    knowledge_runner,
    knowledge_scope,
    knowledge_trace,
    kstg_foreign,
    run_store,
)
from tools.mobile.knowledge_run_args import RunFacts

logger = logging.getLogger("tools.mobile.knowledge_close")
_TASKS: set = set()


def _body(run_id: str) -> dict:
    body = (run_store.read_manifest(run_id) or {}).get("content")
    return body if isinstance(body, dict) else {}


def _learn_here(package: str, run_id: str, rest: tuple = ()) -> None:
    for name in (package, *rest):
        knowledge_learn.learn_run(name, run_id)


async def _chain(package: str, run_id: str, rest: tuple) -> None:
    """ONE job per run: the apps are learned one after the other, so the
    manifest's learn state is never rewritten by two jobs at once (M4)."""
    for name in (package, *rest):
        await knowledge_runner.learn_job(name, run_id)


def schedule(package: str, run_id: str, rest: tuple = ()) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        args = (package, run_id, rest)
        threading.Thread(target=_learn_here, args=args, daemon=True).start()
        return
    task = loop.create_task(_chain(package, run_id, rest))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


_MAX_TESTED = 3
_MIN_STEPS = 2


def tested_packages(run_id: str, package: str) -> list[str]:
    """The apps the run really tested that may own knowledge (D07).

    Ranked by the steps the run drove in each app, most first (ties: first
    seen), so a transient pin never pushes the target past the cap. An app
    needs ``_MIN_STEPS`` steps (a one-step bounce is not tested); the run's own
    declared package always qualifies. Transient and system surfaces never do.
    """
    steps = knowledge_trace.load_steps(run_id)
    counts: dict[str, int] = {}
    for rec in steps:
        name = kstg_foreign.observed(rec)
        counts[name] = counts.get(name, 0) + 1
    counts.setdefault(package, 0)
    named = [n for n in counts if n and knowledge_scope.allowed(n)]
    named = [n for n in named if n == package or counts[n] >= _MIN_STEPS]
    named.sort(key=lambda n: -counts[n])
    return named[:_MAX_TESTED]


def _seed(run_id: str, app: str, declared: str) -> None:
    """A secondary app skipped ``begin_run``: give its runs row the run's env."""
    if app == declared:
        return
    know = _body(run_id).get("knowledge")
    env = know.get("env") if isinstance(know, dict) else {}
    facts = RunFacts(run_id, app, env=env if isinstance(env, dict) else {})
    knowledge_run_store.begin_sync(facts)


def on_close(run_id: str) -> None:
    """Learn from the run that just closed. Never raises, never waits."""
    try:
        package = str(_body(run_id).get("package") or "")
        if not run_id or not package:
            return
        apps = tested_packages(run_id, package)
        for app in apps:
            _seed(run_id, app, package)
        if len(apps) == 1:
            schedule(apps[0], run_id)
        elif apps:
            schedule(apps[0], run_id, tuple(apps[1:]))
        else:
            why = knowledge_scope.knowledge_scope(package) or "no app under test"
            knowledge_run_store.store_state(run_id, "skipped", why)
    except Exception:
        logger.exception("knowledge close: could not schedule learning for %s", run_id)


def learn_line(run_id: str) -> str:
    """``learned`` / ``pending: run still open`` / ``skipped: system package``."""
    body = _body(run_id)
    know = body.get("knowledge")
    if not isinstance(know, dict):
        return ""
    state = str(know.get("learn_state") or "")
    reason = str(know.get("learn_reason") or "")
    if state in ("unlearned", "open") and not isinstance(body.get("final"), dict):
        return "pending: run still open"
    if state == "unlearned":
        return "pending: learning scheduled at close"
    return state + (": " + reason if reason else "")


def off_note(run_id: str) -> str:
    """The start-reply note when the run's package gets no knowledge, else ``""``."""
    why = knowledge_scope.knowledge_scope(_body(run_id).get("package"))
    return (
        "Knowledge is OFF for the pinned package (%s); an app the run moves into"
        " is still learned at close." % why
        if why
        else ""
    )
