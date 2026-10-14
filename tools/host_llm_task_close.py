"""Closing a host task of tools.host_llm (split out of host_llm.py)."""

from __future__ import annotations

import logging

from tools import host_llm_consts as hk
from tools import host_llm_parse as pr
from tools import prep_store

logger = logging.getLogger("tools.host_llm")


def _capped_note() -> dict:
    return {
        "field_chars": hk._MAX_FIELD_CHARS,
        "items": hk._MAX_ITEMS,
        "depth": hk._MAX_DEPTH,
    }


def _closed_content(record: dict, payload: object, raw: str) -> dict:
    """The tagged ``content`` dict ``close_task`` hands back on success."""
    return {
        "kind": str(record.get("kind") or ""),
        "return_field": str(record.get("return_field") or ""),
        # Server-side state bound to this id at open_task time and never
        # round-tripped through the host, so a caller can TRUST which
        # session / description a submission belongs to.
        "meta": record.get("meta") if isinstance(record.get("meta"), dict) else {},
        "payload": payload,
        "raw": str(raw or "")[: hk._MAX_RAW_CHARS],
        # Tagged at the boundary so no caller has to remember to.
        "untrusted": True,
        "model_derived": True,
        "source": "host_model",
        "capped": _capped_note(),
    }


def _wrong_kind(kind: str, expect_kind: str) -> dict | None:
    if expect_kind and kind != str(expect_kind):
        return {
            "error": f"task_id belongs to a {kind!r} task, not {str(expect_kind)!r}",
            "content": None,
        }
    return None


async def _load_open_task(
    tid: str, raw: str, expect_kind: str
) -> tuple[dict | None, dict | None]:
    """Validate *tid* against the store: ``(record, None)`` or ``(None, rejection)``."""
    if len(raw or "") > hk._MAX_RAW_CHARS:
        return None, {
            "error": f"submission exceeds {hk._MAX_RAW_CHARS} characters",
            "content": None,
        }
    loaded = await prep_store.load_prep(tid)
    record = loaded.get("content")
    if loaded.get("error") or not isinstance(record, dict):
        return None, {"error": "unknown or expired task_id", "content": None}
    if record.get(hk._MARKER) != hk._SCHEMA_VERSION:
        return None, {"error": "unknown or expired task_id", "content": None}
    rejection = _wrong_kind(str(record.get("kind") or ""), expect_kind)
    return (None, rejection) if rejection is not None else (record, None)


async def _claim(tid: str) -> bool:
    """True when another close already consumed *tid* (this one lost the race)."""
    # The DELETE is the atomic claim: load_prep above is only a read, so two
    # concurrent closes can both pass validation. Only the one whose DELETE
    # removed the row may proceed; the other is refused like a stale id.
    claim = await prep_store.delete_prep(tid)
    claimed = claim.get("content") if isinstance(claim, dict) else None
    return isinstance(claimed, dict) and claimed.get("deleted") is False


def _parsed_payload(raw: str, return_field: str) -> object:
    parsed = pr._extract_json(raw) if return_field else None
    return pr._cap(parsed) if isinstance(parsed, dict) else None


# Validate a host submission against an open task and consume the task id.
#
# Returns ``{"kind", "return_field", "meta", "payload", "raw", "untrusted",
# "model_derived", "source", "capped"}``. The payload is returned PRE-TAGGED as
# model-derived and untrusted, with every string capped at
# ``_MAX_FIELD_CHARS`` and collections at ``_MAX_ITEMS``, so a caller cannot
# accidentally treat it as trusted server output or let one huge field ride
# into an export, a Jira write or the next prompt. ``payload`` is None when the
# task expected free-form text or the reply carried no parseable JSON object --
# the caller decides whether that is fatal, because only it knows its required
# shape. A stale, unknown, tampered, or wrong-kind id is rejected identically.
# The record is deleted on a successful close, so a ``task_id`` is one-shot.
# Never raises.
async def close_task(task_id: str, raw: str, *, expect_kind: str = "") -> dict:
    """Validate a host submission against an open task and consume the task id."""
    try:
        tid = str(task_id or "")
        if not tid:
            return {"error": "task_id is required", "content": None}
        record, rejection = await _load_open_task(tid, raw, expect_kind)
        if rejection is not None:
            return rejection
        rec: dict = record or {}
        kind = str(rec.get("kind") or "")
        payload = _parsed_payload(raw, str(rec.get("return_field") or ""))
        if await _claim(tid):
            return {"error": "unknown or expired task_id", "content": None}
        logger.info("host_llm: closed %s task %s", kind or "generic", tid)
        return {"error": None, "content": _closed_content(rec, payload, raw)}
    except Exception as exc:
        logger.exception("host_llm.close_task failed")
        return {"error": str(exc), "content": None}
