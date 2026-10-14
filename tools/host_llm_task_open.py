"""Opening a host task of tools.host_llm (split out of host_llm.py)."""

from __future__ import annotations

import logging
from typing import NamedTuple

from tools import host_llm_consts as hk
from tools import host_llm_envelope as env
from tools import host_llm_parse as pr
from tools import prep_store
from tools.host_llm_request import HostTaskRequest

logger = logging.getLogger("tools.host_llm")


class _TaskSpec(NamedTuple):
    kind: str
    return_field: str
    meta: dict | None
    created_by: str | None


async def _persist_task(spec: _TaskSpec) -> tuple[str, str]:
    """Save the task record; return ``(task_id, "")`` or ``("", error)``."""
    record = {
        hk._MARKER: hk._SCHEMA_VERSION,
        "kind": spec.kind,
        "return_field": str(spec.return_field or ""),
        "meta": pr._cap(spec.meta) if isinstance(spec.meta, dict) and spec.meta else {},
    }
    saved = await prep_store.save_prep(record, spec.created_by or hk._CREATED_BY)
    task_id = str(((saved.get("content") or {}) or {}).get("prep_id") or "")
    if saved.get("error") or not task_id:
        return "", str(saved.get("error") or "could not persist the host task")
    return task_id, ""


# (system, user, response_schema, submit_tool)
_Rest = tuple[str, str, "dict | None", str]


def _reply(spec: _TaskSpec, rest: _Rest, task_id: str) -> dict:
    system, user, response_schema, submit_tool = rest
    req = HostTaskRequest(
        spec.kind,
        system,
        user,
        return_field=spec.return_field,
        response_schema=response_schema,
        submit_tool=submit_tool,
    )
    envelope = env.build_envelope(req, task_id)
    return {"error": None, "content": {"task_id": task_id, "envelope": envelope}}


async def _open(spec: _TaskSpec, rest: _Rest) -> dict:
    kind_s = str(spec.kind or "")
    if kind_s not in hk._KNOWN_KINDS:
        return {"error": f"unknown host_llm task kind {kind_s!r}", "content": None}
    spec = spec._replace(kind=kind_s)
    task_id, error = await _persist_task(spec)
    if error:
        return {"error": error, "content": None}
    logger.info("host_llm: opened %s task %s", kind_s, task_id)
    return _reply(spec, rest, task_id)


# Persist a task record and return ``{"task_id": ..., "envelope": {...}}``.
#
# ``meta`` is OPTIONAL server-side state bound to the task id -- a
# ``session_id``, the originating description, a retry round counter. It lives
# on the RECORD and is NEVER placed in the envelope, so the host never sees it
# and cannot supply, guess or alter it: a submission can only ever act on the
# state the SERVER attached to that id (which is why the coach's session_id
# travels this way instead of as a tool parameter -- a host must not be able to
# write findings into another tester's session). It is passed through ``_cap``
# exactly like a parsed submission, so an oversized value cannot bloat the
# record.
#
# Only the task IDENTITY is stored -- the prompt itself rides to the host in the
# envelope and does not need to survive the round trip, which keeps the record
# tiny and well under ``QA_PREP_MAX_BYTES``. The record carries the
# ``__host_llm__`` marker and ``created_by="host_llm"``, so a host-task id is
# always distinguishable from a generation prep id. Never raises.
async def open_task(req: HostTaskRequest) -> dict:
    """Persist a task record and return ``{"task_id": ..., "envelope": {...}}``."""
    try:
        return await _open(
            _TaskSpec(req.kind, req.return_field, req.meta, req.created_by),
            (req.system, req.user, req.response_schema, req.submit_tool),
        )
    except Exception as exc:
        logger.exception("host_llm.open_task failed")
        return {"error": str(exc), "content": None}
