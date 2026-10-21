"""Knowledge follows the app a run hands over to (H22).

The run's snapshot is keyed on the manifest package fixed at run open. When the
exploratory lane hands the run to another app (``explore_runner.handover``) the
manifest's ``knowledge_package`` moves with it and the snapshot is read again for
the new app, so the app the run is really in gets its own memory.
"""

from __future__ import annotations

import logging

from tools.mobile import knowledge_snapshot, run_store

logger = logging.getLogger(__name__)


def follow(run_id: str, package: str) -> None:
    """Point the run's knowledge at *package* and reload its snapshot. Never raises."""
    try:
        read = run_store.read_manifest(run_id) or {}
        body = read.get("content")
        if read.get("error") or not isinstance(body, dict):
            return
        if knowledge_snapshot.package_of(body) != package:
            body["knowledge_package"] = package
            run_store.write_manifest(run_id, body)
        knowledge_snapshot.load_for_run(run_id)
    except Exception:
        logger.exception("knowledge handover: follow failed for %s", run_id)
