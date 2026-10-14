"""Load stage of qa_submit_suite: open the staged prep and note version skew.

Moved from handlers/submit_suite.py (C3a step 1). A build mismatch only warns:
blocking would discard the tester's generation work for a usually harmless
difference, while silence let a split-version flow go unnoticed. The note
names both causes (self-update, or two installs with separate ``.env`` files)
and appends the same-version-different-code case when it applies.
"""

from __future__ import annotations

import logging

from agents.host_prep_serde import PrepSerdeError
from agents.host_submit_ports_prep import SubmitPorts
from agents.host_submit_types import SubPrep
from config.settings_guard import never_raise

logger = logging.getLogger("agents.host_mode")


def prep_version_note(meta: dict, ports: SubmitPorts) -> str:
    version_note = ""
    with never_raise(logger, "prep version check failed", exc_info=True):
        wrote = str(meta.get("app_version") or "")
        boot = ports.common.boot_version()
        if wrote and boot and wrote != boot:
            version_note = ports.prep_load.version_skew_note(wrote, boot)
        version_note += ports.prep_load.code_skew_note(
            str(meta.get("code_fingerprint") or ""),
            ports.io.code_fingerprint(),
            version_matched=bool(wrote and wrote == boot),
        )
    return version_note


def _unreadable_reply(prep_id: str) -> str:
    return (
        f"⚠️ The staged preparation for prep_id `{prep_id}` could "
        "not be read (it may be corrupted or from an incompatible version). "
        "Start again with `qa_prepare_test_cases`."
    )


# Fix 5b: without the finalized refusal, a resubmit would reach the
# sidecar/merge branches and finalize the same prep twice.
async def load_prep(prep_id: str, ports: SubmitPorts) -> str | SubPrep:
    envelope, refusal = await ports.prep_load.load_open_prep(prep_id, require_dict=True)
    if refusal:
        return refusal
    try:
        prepared = ports.io.deserialize_prepared(envelope.get("prepared") or {})
    except PrepSerdeError:
        logger.warning("host-mode submit: prep rehydrate failed", exc_info=True)
        return _unreadable_reply(prep_id)
    meta = envelope.get("meta") or {}
    source_text = str(meta.get("source_text") or "")
    source_url = str(meta.get("source_url") or "") or None
    version_note = prep_version_note(meta, ports)
    return SubPrep(
        prep_id, envelope, prepared, meta, source_text, source_url, version_note
    )
