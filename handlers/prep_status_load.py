"""prep_status leaf: load a prep and the shared refusal replies."""

from __future__ import annotations

import logging
import sys
from typing import Any

from agents.host_prep_reply_text import (
    finalized_text,
    host_task_text,
    missing_text,
    store_error_text,
)
from config.settings_guard import never_raise
from handlers.prep_status_host import host
from handlers.seam_alias import alias
from tools import host_llm, prep_store

logger = logging.getLogger("tools.mcp_handlers")


def prep_store_error_reply(prep_id: str, result: Any) -> str:
    """Reply for a prep-store READ that FAILED, or "" when it did not (F6).

    A corrupt or unreadable store is RETRYABLE, so it must not read as a missing
    prep. Never raises.
    """
    try:
        err = str((result or {}).get("error") or "").strip()
    except AttributeError:  # pragma: no cover - defensive
        return ""
    return store_error_text(prep_id, err) if err else ""


def _prep_missing_reply(prep_id: str) -> str:
    """One message covering unknown / TTL-expired / already-finalized prep_ids."""
    return missing_text(prep_id)


def _finalized_reply(prep_id: str, record: Any) -> str:
    """Reply for a prep whose suite ALREADY finalized successfully, or "".

    "" when the record is not a finalized prep, so callers use it as a guard
    like _host_task_reply (Fix 5b, 2026-08-03). Never raises.
    """
    with never_raise(
        logger, "_finalized_reply failed", level=logging.ERROR, exc_info=True
    ):
        if not prep_store.is_finalized_record(record):
            return ""
        info = record.get(prep_store.FINALIZED_KEY) or {}
        suite_id = str(info.get("suite_id") or "").strip()
        export_path = str(info.get("export_path") or "").strip()
        return finalized_text(prep_id, suite_id, export_path)
    return ""


def _host_task_reply(prep_id: str, record: object) -> str:
    """Reply naming the id KIND when a loaded prep record is a host_llm task.

    Host-task records share the preps table with generation preps; one helper at
    ALL FOUR load sites so they cannot drift. "" when not a host task.
    Never raises.
    """
    with never_raise() as g:
        if not host_llm.is_host_task_record(record):
            return ""
    if g.failed:
        return ""
    return host_task_text(prep_id)


def _open_refusal(prep_id: str, loaded: dict, require_dict: bool) -> str:
    """Host-task, store-failure, then missing-prep refusal for a loaded prep, or ""."""
    h = host()
    envelope = loaded.get("content")
    refusal = h._host_task_reply(prep_id, envelope) or h.prep_store_error_reply(
        prep_id, loaded
    )
    if refusal:
        return refusal
    if not isinstance(envelope, dict) if require_dict else envelope is None:
        return h._prep_missing_reply(prep_id)
    return ""


async def load_open_prep(
    prep_id: str, *, require_dict: bool = False, wrap_final=None
) -> tuple[Any, str]:
    """Load a prep and run the shared refusals: ``(envelope, "")`` or ``(None, reply)``.

    Order: host-task id, store failure, no prep, finalized. Raises load_prep's errors.
    """
    loaded = await prep_store.load_prep(prep_id)
    envelope = loaded.get("content")
    refusal = _open_refusal(prep_id, loaded, require_dict)
    if refusal:
        return None, refusal
    final_note = host()._finalized_reply(prep_id, envelope)
    if final_note:
        return None, wrap_final(prep_id, final_note) if wrap_final else final_note
    return envelope, ""


_prep_store_error_reply = alias(sys.modules[__name__], "prep_store_error_reply")
