"""Learning stage versions: flag on a new app version, verify on use, remap, stale."""

from __future__ import annotations

from tools.mobile import knowledge_sk3_alias as sk3_alias
from tools.mobile import knowledge_sk_legacy as sk_legacy
from tools.mobile import knowledge_versions as kv
from tools.mobile.knowledge_learn import StageResult


def _versioned(conn, lc, version: str) -> int:
    done = kv.remap_screens(conn, version, lc.run_id)
    if kv.is_new_version(conn, lc.run_id, version):
        done += kv.flag_version(conn, version, lc.run_id)
    else:
        done += kv.reactivate_downgrade(conn, version, lc.run_id)
    done += kv.restore_confirmed(conn, lc.steps or [], kv.VerCtx(version, lc.run_id))
    done += kv.apply_missing(conn, lc.steps or [], kv.VerCtx(version, lc.run_id))
    return done


def run(conn, lc) -> StageResult:
    """Never raises past the pipeline; one transaction per call (the pipeline's)."""
    result = StageResult("versions")
    version = str(getattr(lc.facts, "app_version", "") or "")
    if version:
        result.wrote += _versioned(conn, lc, version)
    else:
        result.notes.append("no app version")
    result.wrote += sk_legacy.migrate(conn, lc.package, lc.run_id)
    result.wrote += sk3_alias.migrate(conn, lc.run_id)
    result.wrote += sk_legacy.retire_stale(conn, lc.package, lc.run_id)
    result.wrote += kv.stale_shortcuts(conn, lc.run_id)
    return result
