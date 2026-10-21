"""Learning at the run's close (C11): ONE hook in ``run_finalize.finalize``.

Every close path (done, stop, idle_timeout, server_died, released_idle) ends in
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
    run_store,
)

logger = logging.getLogger("tools.mobile.knowledge_close")
_TASKS: set = set()


def _body(run_id: str) -> dict:
    body = (run_store.read_manifest(run_id) or {}).get("content")
    return body if isinstance(body, dict) else {}


def _learn_here(package: str, run_id: str) -> None:
    knowledge_learn.learn_run(package, run_id)


def schedule(package: str, run_id: str) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        args = (package, run_id)
        threading.Thread(target=_learn_here, args=args, daemon=True).start()
        return
    task = loop.create_task(knowledge_runner.after_result(package, run_id))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


def on_close(run_id: str) -> None:
    """Learn from the run that just closed. Never raises, never waits."""
    try:
        package = str(_body(run_id).get("package") or "")
        if not run_id or not package:
            return
        why = knowledge_scope.knowledge_scope(package)
        if why:
            knowledge_run_store.store_state(run_id, "skipped", why)
        else:
            schedule(package, run_id)
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
    if state == "unlearned":
        return "pending: learning scheduled at close"
    return state + (": " + reason if reason else "")


def off_note(run_id: str) -> str:
    """The start-reply note when the run's package gets no knowledge, else ``""``."""
    why = knowledge_scope.knowledge_scope(_body(run_id).get("package"))
    return (
        "Knowledge is OFF for this run (%s): nothing is learned or replayed." % why
        if why
        else ""
    )
