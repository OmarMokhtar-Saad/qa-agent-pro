"""Pending-generation store — SQLite-backed, never-raises (host-mode Phase 1).

Host-mode ("boomerang") test-case generation splits the pipeline across TWO
MCP tool calls: ``qa_prepare_test_cases`` builds a grounded prompt and hands it
to the tester's own chat model, then ``qa_submit_suite`` validates the JSON the
host produced. Because those are two separate stateless tool calls, the prepared
context (grounded prompt, checklist, category specs, case-count bounds, ticket
provenance) must persist BETWEEN them so submit validates against what was
actually prepared — and so a stale, unknown, or tampered ``prep_id`` is rejected
rather than trusted.

This module persists each prep record (and, for weaker hosts that submit one
category at a time, its incremental per-category submissions) to the SAME
SQLite file ``tools/suite_store.py`` uses, in its own tables. It mirrors
suite_store's never-raise contract exactly:

  On success: {"error": None, "content": <value>}
  On failure: {"error": str, "content": None}

All blocking sqlite I/O runs in ``asyncio.to_thread()``. Records carry a
creation timestamp and are expired on read (TTL, ``QA_PREP_TTL_S``). ``prep_id``
is an unguessable uuid4 hex, so an unknown/tampered id simply misses and load
returns ``None`` — the caller (``qa_submit_suite``) then refuses to finalize.
Payloads over ``QA_PREP_MAX_BYTES`` are rejected at save time so a host cannot
wedge the store.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
import uuid
from pathlib import Path

from config.settings import settings
from tools.install_paths import resolve_data_path

logger = logging.getLogger(__name__)

_DEFAULT_TTL_S = 3600
_DEFAULT_MAX_BYTES = 4_000_000
# Hard cap on TOTAL prep lifetime under the sliding TTL (see _expired):
# touch refreshes restart the TTL clock but can never push a prep past
# created_at + this. 4x the default TTL.
_DEFAULT_MAX_LIFETIME_S = 14_400

# Seconds a contended write waits before giving up. Host mode means several MCP
# host processes share this DB file, so a short wait beats an instant failure.
_BUSY_TIMEOUT_S = 5.0


_SCHEMA = """
CREATE TABLE IF NOT EXISTS preps (
    id           TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    created_at   REAL NOT NULL,
    created_by   TEXT,
    touched_at   REAL
);
CREATE TABLE IF NOT EXISTS prep_submissions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    prep_id       TEXT NOT NULL,
    category_name TEXT NOT NULL,
    payload_json  TEXT NOT NULL,
    created_at    REAL NOT NULL,
    UNIQUE(prep_id, category_name),
    FOREIGN KEY (prep_id) REFERENCES preps(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_prep_submissions_prep_id
    ON prep_submissions(prep_id);
"""


def _db_path() -> Path:
    # Anchored to the install root, not the process cwd: a prep written while one
    # project was open used to be invisible when another was, which the host reads
    # as "prep not found -- start again" and answers with a full re-generation.
    return resolve_data_path(settings.qa_suite_store_path)


def _ttl_seconds() -> float:
    """TTL after which a prep record is expired on read. Never raises."""
    val = getattr(settings, "qa_prep_ttl_s", _DEFAULT_TTL_S)
    try:
        val = int(val)
    except (TypeError, ValueError):
        return float(_DEFAULT_TTL_S)
    return float(val) if val > 0 else float(_DEFAULT_TTL_S)


def _has_touched_at(conn: sqlite3.Connection) -> bool:
    """Whether preps.touched_at exists on THIS database. Probe, never raises."""
    try:
        rows = conn.execute("PRAGMA table_info(preps)").fetchall()
    except sqlite3.Error:
        return False
    return any(r[1] == "touched_at" for r in rows)


def _alter_add_touched_at(conn: sqlite3.Connection) -> None:
    """The single ALTER, isolated so a test can simulate a locked database."""
    conn.execute("ALTER TABLE preps ADD COLUMN touched_at REAL")


def _ensure_touched_at(conn: sqlite3.Connection) -> bool:
    """Add preps.touched_at to a DB created before the sliding TTL existed.

    PROBE FIRST, and report back whether the column is usable. A bare
    ``except sqlite3.OperationalError: pass`` around the ALTER would also
    swallow "database is locked" -- which this module's docstring calls the
    NORMAL concurrent multi-client case -- and every later
    ``SELECT ... touched_at`` would then raise "no such column", which
    load_prep converts to a miss: i.e. every LIVE prep would read as unknown
    or expired, the exact loss this change exists to prevent. Never raises.
    """
    if _has_touched_at(conn):
        return True
    try:
        with conn:  # transaction
            _alter_add_touched_at(conn)
    except sqlite3.Error:
        # Locked/read-only DB, or a racing process won -- re-probe, never guess.
        logger.debug("prep_store: touched_at migration deferred", exc_info=True)
        return _has_touched_at(conn)
    return True


def _sliding_ttl_on() -> bool:
    """QA_PREP_SLIDING_TTL_ENABLED, read never-raise. OFF => fixed TTL, unchanged."""
    try:
        return bool(getattr(settings, "qa_prep_sliding_ttl_enabled", False))
    except Exception:
        return False


def _touch_enabled() -> bool:
    """Whether an activity touch is RECORDED in preps.touched_at.

    TRUE under EITHER flag, deliberately. The sliding TTL needs the timestamp
    to enforce a refreshed clock; DISCLOSURE needs it to see the incident at
    all -- the 2026-07-31 shape was 8 worker packets fetched via
    qa_get_category_job and ZERO qa_submit_category calls, so staged == 0 and
    touched_at is the only evidence the run ever existed. Gating the write on
    the sliding TTL alone made QA_PREP_DISCLOSE_UNFINISHED unable to disclose
    the very incident it exists for unless a second, unrelated flag was also
    on. Writing the column costs nothing while TTL enforcement stays off:
    _expired() reads touched_at only when _sliding_ttl_on(). Never raises."""
    try:
        return bool(
            getattr(settings, "qa_prep_sliding_ttl_enabled", False)
            or getattr(settings, "qa_prep_disclose_unfinished", False)
        )
    except Exception:
        return False


def _max_lifetime_s() -> float:
    """Hard cap on a prep's TOTAL lifetime under the sliding TTL, so touch
    refreshes cannot extend a prep forever (the same anti-extension stance
    update_prep takes for the gap loop). Never raises."""
    val = getattr(settings, "qa_prep_max_lifetime_s", _DEFAULT_MAX_LIFETIME_S)
    try:
        val = int(val)
    except (TypeError, ValueError):
        return float(_DEFAULT_MAX_LIFETIME_S)
    return float(val) if val > 0 else float(_DEFAULT_MAX_LIFETIME_S)


def _expired(created_at: float, touched_at: object) -> bool:
    """TTL decision for one prep row. Fixed TTL from created_at by default;
    with QA_PREP_SLIDING_TTL_ENABLED the clock restarts at the last touch
    (qa_get_category_job / qa_submit_category), bounded by _max_lifetime_s()
    from creation. A NULL/absent touched_at simply degrades to the fixed TTL,
    so a deferred migration can never expire a live prep. Never raises."""
    now = time.time()
    try:
        anchor = created_at
        if _sliding_ttl_on():
            try:
                t = float(touched_at or 0.0)
            except (TypeError, ValueError):
                t = 0.0
            anchor = max(created_at, t)
            if (now - created_at) > _max_lifetime_s():
                return True
        return (now - anchor) > _ttl_seconds()
    except Exception:  # pragma: no cover -- arithmetic on floats
        return (now - created_at) > _ttl_seconds()


def _max_bytes() -> int:
    """Max serialized payload size accepted by save_prep/save_submission."""
    val = getattr(settings, "qa_prep_max_bytes", _DEFAULT_MAX_BYTES)
    try:
        val = int(val)
    except (TypeError, ValueError):
        return _DEFAULT_MAX_BYTES
    return val if val > 0 else _DEFAULT_MAX_BYTES


def _connect() -> sqlite3.Connection:
    """Open the DB (creating parent dirs + schema). Caller must close.

    PRAGMA foreign_keys = ON so the declared ON DELETE CASCADE on
    prep_submissions actually fires (SQLite defaults it OFF per connection).

    journal_mode = WAL + a busy_timeout because host mode makes CONCURRENT
    multi-client use the normal case, not the exception: each MCP host (Claude
    Desktop, Cursor, ChatGPT, ...) runs its own server process against this same
    SQLite file, so two testers preparing/submitting at the same moment would
    otherwise race on the default rollback journal and surface "database is
    locked". WAL lets readers and one writer proceed concurrently; the timeout
    makes a genuinely contended write wait instead of failing instantly. Both
    pragmas are best-effort — an older SQLite or a filesystem that cannot do WAL
    must not break the store, so a failure here is logged and ignored (the
    never-raise contract still holds via the callers' error dicts).
    """
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=_BUSY_TIMEOUT_S)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute(f"PRAGMA busy_timeout = {int(_BUSY_TIMEOUT_S * 1000)}")
    except sqlite3.Error:
        logger.debug("prep_store: WAL/busy_timeout pragmas unavailable", exc_info=True)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    # Best-effort migration for DBs created before touched_at existed.
    # Probe-first and non-fatal: readers re-probe and degrade to the
    # fixed TTL rather than ever selecting a missing column.
    _ensure_touched_at(conn)
    return conn


# --------------------------------------------------------------------------- #
# Sync workers (run inside asyncio.to_thread)
# --------------------------------------------------------------------------- #


def _save_prep_sync(payload: dict, created_by: str | None) -> str:
    prep_id = uuid.uuid4().hex
    now = time.time()
    conn = _connect()
    try:
        with conn:  # transaction
            conn.execute(
                "INSERT INTO preps (id, payload_json, created_at, created_by) "
                "VALUES (?, ?, ?, ?)",
                (prep_id, json.dumps(payload), now, created_by),
            )
        return prep_id
    finally:
        conn.close()


def _load_prep_sync(prep_id: str) -> dict | None:
    conn = _connect()
    try:
        # NEVER assume the migration ran: on a locked DB it is deferred, and
        # selecting a missing column would raise "no such column" -- which this
        # function converts into a miss, i.e. a LIVE prep reported as unknown.
        _sql = "SELECT payload_json, created_at, touched_at FROM preps WHERE id = ?"
        if not _has_touched_at(conn):
            _sql = "SELECT payload_json, created_at, NULL FROM preps WHERE id = ?"
        row = conn.execute(_sql, (prep_id,)).fetchone()
        if row is None:
            return None
        if _expired(float(row[1]), row[2]):
            # Expired — delete it (and its submissions, via cascade) and miss.
            with conn:
                conn.execute("DELETE FROM preps WHERE id = ?", (prep_id,))
            return None
    finally:
        conn.close()
    try:
        return json.loads(row[0])
    except (ValueError, TypeError):
        return None


def _delete_prep_sync(prep_id: str) -> None:
    conn = _connect()
    try:
        with conn:  # transaction
            conn.execute("DELETE FROM preps WHERE id = ?", (prep_id,))
    finally:
        conn.close()


# Envelope key stamped on a prep whose suite FINALIZED successfully. 2026-08-03
# (Fix 5b): finalize used to DELETE the prep, so "prep gone" meant three different
# things -- unknown id, expired TTL, and finished work -- and every reader keyed off
# that one signal. A host that resubmitted after a successful finalize was told to
# "start again with qa_prepare_test_cases", i.e. to regenerate a suite that already
# existed. Keeping the record and stamping it lets each reader answer precisely.
#
# RETENTION NOTE: the payload holds the ticket source text, so keeping it until the
# TTL lengthens how long that content lives here versus deleting at finalize. The
# stamp itself carries only a suite_id and an export path, and the TTL still reaps
# the record -- _expired reads created_at/touched_at, and update_prep leaves
# created_at untouched, so a stamped prep can never become immortal.
FINALIZED_KEY = "finalized"


def is_finalized_record(record: object) -> bool:
    """Whether a loaded prep envelope belongs to an already-finalized suite.

    Mirrors tools.host_llm.is_host_task_record: a cheap, never-raising shape test
    the handlers can run at every load site before rehydrating anything.
    """
    try:
        return isinstance(record, dict) and isinstance(record.get(FINALIZED_KEY), dict)
    except Exception:  # pragma: no cover - defensive
        return False


def _update_prep_sync(prep_id: str, payload: dict) -> bool:
    now_payload = json.dumps(payload)
    conn = _connect()
    try:
        with conn:  # transaction
            cur = conn.execute(
                "UPDATE preps SET payload_json = ? WHERE id = ?",
                (now_payload, prep_id),
            )
        return (cur.rowcount or 0) > 0
    finally:
        conn.close()


def _save_submission_sync(prep_id: str, category_name: str, payload: dict) -> int:
    now = time.time()
    conn = _connect()
    try:
        with conn:  # transaction
            cur = conn.execute(
                "INSERT OR REPLACE INTO prep_submissions "
                "(prep_id, category_name, payload_json, created_at) "
                "VALUES (?, ?, ?, ?)",
                (prep_id, category_name, json.dumps(payload), now),
            )
            return int(cur.lastrowid or 0)
    finally:
        conn.close()


def _load_submissions_sync(prep_id: str) -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT category_name, payload_json FROM prep_submissions "
            "WHERE prep_id = ? ORDER BY id ASC",
            (prep_id,),
        ).fetchall()
    finally:
        conn.close()
    out: list[dict] = []
    for name, payload_json in rows:
        try:
            out.append({"category_name": name, "payload": json.loads(payload_json)})
        except (ValueError, TypeError):
            continue
    return out


def _touch_prep_sync(prep_id: str) -> bool:
    now = time.time()
    conn = _connect()
    try:
        if not _has_touched_at(conn):
            # Migration deferred (locked DB): skip silently, the caller keeps
            # the fixed TTL rather than failing the orchestration step.
            return False
        with conn:  # transaction
            cur = conn.execute(
                "UPDATE preps SET touched_at = ? WHERE id = ?", (now, prep_id)
            )
        return (cur.rowcount or 0) > 0
    finally:
        conn.close()


def _list_unfinished_sync(limit: int) -> list[dict]:
    """Non-expired preps with real activity (touched or >=1 staged row).

    Newest first. The activity filter runs in SQL; only the TTL decision is
    left to Python (it depends on the sliding-TTL flag). ACCEPTED BOUND: the
    query takes 4x limit candidates, so with more than that many recent ACTIVE
    preps an older still-live one can fall outside the window -- acceptable for
    a newest-first disclosure that never blocks anything. payload_json is
    parsed ONLY for rows that survive the filters (bounded by limit) to read
    meta.expected_categories for the N/8 denominator. ACCEPTED SCOPE: the
    existing preps.created_by column is deliberately NOT filtered on -- this
    store is one SQLite file per install/user, so there is no cross-tenant
    separation to enforce, and the disclosure is flag-gated OFF by default."""
    lim = max(1, int(limit))
    conn = _connect()
    try:
        touch_col = "p.touched_at" if _has_touched_at(conn) else "NULL"
        rows = conn.execute(
            "SELECT p.id, p.created_at, " + touch_col + ", p.payload_json, "
            "COUNT(s.id) AS staged FROM preps p "
            "LEFT JOIN prep_submissions s ON s.prep_id = p.id "
            "GROUP BY p.id "
            "HAVING staged > 0 OR " + touch_col + " IS NOT NULL "
            "ORDER BY p.created_at DESC LIMIT ?",
            (lim * 4,),
        ).fetchall()
    finally:
        conn.close()
    out: list[dict] = []
    for pid, created, touched, payload_json, staged in rows:
        created_f = float(created)
        if _expired(created_f, touched):
            continue
        # Fix 5b: a FINALIZED prep is finished work, never resumable. Dropping its
        # staged rows would NOT be enough -- the SQL activity filter also passes on
        # `touched_at IS NOT NULL`, and handle_get_category_job touches every prep
        # on the fan-out path, so a finished prep would still be offered here and
        # _unfinished_preps_note would tell the tester to resume it with
        # qa_submit_category. Filtered on the stamp instead.
        try:
            if is_finalized_record(json.loads(payload_json) or {}):
                continue
        except Exception:
            logger.debug("could not read prep %s for a finalized check", pid)
        try:
            touched_f = float(touched or 0.0)
        except (TypeError, ValueError):
            touched_f = 0.0
        anchor = max(created_f, touched_f)
        if _sliding_ttl_on():
            expires = min(anchor + _ttl_seconds(), created_f + _max_lifetime_s())
        else:
            expires = created_f + _ttl_seconds()
        expected = 0
        try:
            meta = (json.loads(payload_json) or {}).get("meta") or {}
            expected = len(meta.get("expected_categories") or [])
        except (ValueError, TypeError, AttributeError):
            expected = 0
        out.append(
            {
                "prep_id": pid,
                "created_at": created_f,
                "touched_at": touched_f,
                "staged_count": int(staged or 0),
                "expected_count": expected,
                "expires_at": expires,
            }
        )
        if len(out) >= lim:
            break
    return out


# --------------------------------------------------------------------------- #
# Public async API (never raises)
# --------------------------------------------------------------------------- #


async def save_prep(payload: dict, created_by: str | None = None) -> dict:
    """Persist a prepared-generation record. Returns {"content": {"prep_id": ...}}.

    Rejects an over-large payload (``QA_PREP_MAX_BYTES``) so a host cannot wedge
    the store with a pathological submission. Never raises.
    """
    try:
        blob = json.dumps(payload or {})
        if len(blob.encode("utf-8")) > _max_bytes():
            return {
                "error": f"prep payload exceeds {_max_bytes()} bytes",
                "content": None,
            }
        prep_id = await asyncio.to_thread(_save_prep_sync, payload or {}, created_by)
        logger.info("prep_store: saved prep %s", prep_id)
        return {"error": None, "content": {"prep_id": prep_id}}
    except Exception as exc:
        logger.exception("prep_store.save_prep failed")
        return {"error": str(exc), "content": None}


async def load_prep(prep_id: str) -> dict:
    """Load a prep record by id. Returns {"content": dict|None}.

    Content is ``None`` for an unknown, tampered (uuid miss), or EXPIRED id (an
    expired record is deleted on this read). Never raises.
    """
    try:
        if not prep_id:
            return {"error": None, "content": None}
        payload = await asyncio.to_thread(_load_prep_sync, prep_id)
        return {"error": None, "content": payload}
    except Exception as exc:
        logger.exception("prep_store.load_prep failed")
        return {"error": str(exc), "content": None}


async def delete_prep(prep_id: str) -> dict:
    """Delete a prep record (and its submissions, via cascade) once finalized."""
    try:
        if prep_id:
            await asyncio.to_thread(_delete_prep_sync, prep_id)
        return {"error": None, "content": {"prep_id": prep_id}}
    except Exception as exc:
        logger.exception("prep_store.delete_prep failed")
        return {"error": str(exc), "content": None}


async def update_prep(prep_id: str, payload: dict) -> dict:
    """Overwrite an existing prep record's payload IN PLACE (host-mode gap loop).

    Used to persist the incremented gap-loop round in the envelope's ``meta`` so a
    host cannot ping-pong forever. Deliberately keeps ``created_at`` unchanged, so
    the TTL still counts from prepare and a looping host cannot extend it by
    resubmitting. Rejects an over-large payload and a missing/unknown/expired
    ``prep_id`` (the UPDATE simply matches no row). Never raises.
    """
    try:
        if not prep_id:
            return {"error": "prep_id is required", "content": None}
        blob = json.dumps(payload or {})
        if len(blob.encode("utf-8")) > _max_bytes():
            return {
                "error": f"prep payload exceeds {_max_bytes()} bytes",
                "content": None,
            }
        updated = await asyncio.to_thread(_update_prep_sync, prep_id, payload or {})
        if not updated:
            return {"error": "unknown or expired prep_id", "content": None}
        return {"error": None, "content": {"prep_id": prep_id}}
    except Exception as exc:
        logger.exception("prep_store.update_prep failed")
        return {"error": str(exc), "content": None}


async def save_submission(prep_id: str, category_name: str, payload: dict) -> dict:
    """Persist ONE per-category host submission (incremental submit path).

    Keyed UNIQUE per (prep_id, category_name) so a re-submitted category
    REPLACES the earlier one rather than duplicating it. Rejects an over-large
    payload and an unknown/expired ``prep_id`` (checked first for a clear error,
    ahead of the FK constraint). Never raises.
    """
    try:
        if not prep_id or not category_name:
            return {
                "error": "prep_id and category_name are required",
                "content": None,
            }
        blob = json.dumps(payload or {})
        if len(blob.encode("utf-8")) > _max_bytes():
            return {
                "error": f"submission payload exceeds {_max_bytes()} bytes",
                "content": None,
            }
        existing = await asyncio.to_thread(_load_prep_sync, prep_id)
        if existing is None:
            return {"error": "unknown or expired prep_id", "content": None}
        sub_id = await asyncio.to_thread(
            _save_submission_sync, prep_id, category_name, payload or {}
        )
        if _touch_enabled():
            # Real orchestration activity: restart the TTL clock so an active
            # parallel fan-out cannot expire mid-run (2026-07-31 incident), and
            # mark the prep as ACTIVE for the disclosure listing. Either flag
            # alone is enough to want the timestamp -- see _touch_enabled.
            await asyncio.to_thread(_touch_prep_sync, prep_id)
        return {"error": None, "content": {"submission_id": sub_id}}
    except Exception as exc:
        logger.exception("prep_store.save_submission failed")
        return {"error": str(exc), "content": None}


async def load_submissions(prep_id: str) -> dict:
    """Load all accumulated per-category submissions for a prep_id (ordered)."""
    try:
        if not prep_id:
            return {"error": None, "content": []}
        rows = await asyncio.to_thread(_load_submissions_sync, prep_id)
        return {"error": None, "content": rows}
    except Exception as exc:
        logger.exception("prep_store.load_submissions failed")
        return {"error": str(exc), "content": None}


async def touch_prep(prep_id: str) -> dict:
    """Record real orchestration activity (qa_get_category_job /
    qa_submit_category) on a prep.

    NO-OP unless QA_PREP_SLIDING_TTL_ENABLED **or**
    QA_PREP_DISCLOSE_UNFINISHED is on (see _touch_enabled: disclosure needs
    the touch to see a fetched-packet-only run, which is exactly the
    2026-07-31 incident shape). The TTL clock only actually slides under
    QA_PREP_SLIDING_TTL_ENABLED, and total lifetime stays bounded by
    QA_PREP_MAX_LIFETIME_S (see _expired). Never raises."""
    try:
        if not prep_id or not _touch_enabled():
            return {"error": None, "content": None}
        ok = await asyncio.to_thread(_touch_prep_sync, prep_id)
        return {"error": None, "content": {"touched": bool(ok)}}
    except Exception as exc:
        logger.exception("prep_store.touch_prep failed")
        return {"error": str(exc), "content": None}


def _find_recent_prep_sync(source_url: str, window_s: float) -> dict | None:
    now = time.time()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, payload_json, created_at FROM preps "
            "ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
    finally:
        conn.close()
    for pid, payload_json, created in rows:
        try:
            created_f = float(created)
        except (TypeError, ValueError):
            continue
        if window_s and (now - created_f) > window_s:
            break  # ordered DESC, so everything older is out of the window too
        try:
            env = json.loads(payload_json) or {}
        except (ValueError, TypeError):
            continue
        if is_finalized_record(env):
            continue  # finished work is the SUITE guard's business, not this one
        meta = env.get("meta") or {}
        if str(meta.get("source_url") or "") != source_url:
            continue
        # 2026-08-09: the IMAGE state of that open prep travels with the hit,
        # so tools/mcp_handlers.py can carry its screens forward into a
        # re-prepare -- or refuse and NAME them -- instead of silently
        # generating ungrounded. IDS AND COUNTS ONLY: image bytes are never
        # persisted here. Every field is coerced defensively, because this whole
        # lookup is a best-effort guard: a malformed meta must degrade to "no
        # images", never raise. The isinstance checks matter -- a stray STRING
        # would otherwise iterate into a list of characters.
        raw_ids = meta.get("capture_ids")
        cap_ids = [
            str(x or "").strip()[:64]
            for x in (raw_ids if isinstance(raw_ids, (list, tuple)) else [])
            if str(x or "").strip()
        ][:24]
        raw_labels = meta.get("captured_image_labels")
        cap_labels = [
            str(x or "").strip()
            for x in (raw_labels if isinstance(raw_labels, (list, tuple)) else [])
            if str(x or "").strip()
        ][:8]
        try:
            captured_n = max(0, min(99, int(meta.get("captured_image_count") or 0)))
        except (TypeError, ValueError):
            captured_n = 0
        try:
            attached_n = max(0, min(99, int(meta.get("attached_image_count") or 0)))
        except (TypeError, ValueError):
            attached_n = 0
        return {
            "prep_id": pid,
            "created_at": created_f,
            "age_s": max(0.0, now - created_f),
            "captured_image_count": captured_n,
            "attached_image_count": attached_n,
            "capture_ids": cap_ids,
            "captured_image_labels": cap_labels,
            "host_image_job": bool(meta.get("host_image_job")),
        }
    return None


async def find_recent_prep_by_source(source_url: str, window_s: float = 1800) -> dict:
    """A recent, still-unfinalized prep for the SAME source_url, or None content.

    2026-08-03. The duplicate-prep guard was named for preps but only ever looked
    at finished SUITES (``_find_recent_duplicate_suite``). So the exact case it
    exists for -- two `qa_prepare_test_cases` calls in a row for one source, before
    any suite exists -- was invisible to it. A real run made two preps 43 seconds
    apart with byte-identical source_url and source_text, got no warning, and threw
    away a full preparation.

    Deliberately keyed on exact ``meta.source_url`` (same rule the suite guard
    uses), so free-text descriptions -- which have no stable identity -- are never
    flagged. FINALIZED preps are skipped: those are a completed generation and
    belong to the suite guard, which can report the suite id and case count.

    Scans at most the 20 newest preps and stops at the first one outside the
    window, so cost does not grow with history. Never raises.

    2026-08-09: the hit also carries that prep's IMAGE state --
    ``captured_image_count`` / ``attached_image_count`` / ``capture_ids`` /
    ``captured_image_labels`` / ``host_image_job`` -- so the caller can carry
    device screens forward into a re-prepare, or refuse and name what would
    otherwise vanish. Ids and counts only: no image bytes are stored here, and
    an OLD envelope that predates those keys simply reports zeros and empties.
    """
    try:
        url = str(source_url or "").strip()
        if not url:
            return {"error": None, "content": None}
        hit = await asyncio.to_thread(
            _find_recent_prep_sync, url, float(max(0, window_s))
        )
        return {"error": None, "content": hit}
    except Exception as exc:
        logger.exception("prep_store.find_recent_prep_by_source failed")
        return {"error": str(exc), "content": None}


def _find_prep_snapshot_sync(source_url: str, window_s: float) -> dict | None:
    now = time.time()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, payload_json, created_at FROM preps "
            "ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
    finally:
        conn.close()
    for pid, payload_json, created in rows:
        try:
            created_f = float(created)
        except (TypeError, ValueError):
            continue
        if window_s and (now - created_f) > window_s:
            break  # ordered DESC, so everything older is out of the window too
        try:
            env = json.loads(payload_json) or {}
        except (ValueError, TypeError):
            continue
        meta = env.get("meta") or {}
        if str(meta.get("source_url") or "") != source_url:
            continue
        stamp = str(meta.get("jira_updated") or "").strip()[:64]
        if not stamp:
            continue  # a prep written before the stamp existed proves nothing
        return {
            "prep_id": pid,
            "created_at": created_f,
            "age_s": max(0.0, now - created_f),
            "jira_updated": stamp,
        }
    return None


async def find_prep_snapshot_by_source(
    source_url: str, window_s: float = 86400
) -> dict:
    """The newest prep for *source_url* that stamped a Jira ``fields.updated``.

    2026-08-10 (I2c). Deliberately NOT `find_recent_prep_by_source`, for two
    reasons: that lookup SKIPS finalized preps -- and a finished generation is
    exactly the snapshot a later call has to be compared against -- and it is
    keyed to the duplicate-prep guard's window, whereas this is called with
    QA_PREP_TTL_S so the staleness check is independent of that guard's flag.

    Preps with no `jira_updated` stamp are skipped rather than returned empty,
    so an envelope written before the key existed can never be mistaken for a
    fresher snapshot. Same bounded 20-row scan as the guard; ids and one short
    timestamp only. Never raises.
    """
    try:
        url = str(source_url or "").strip()
        if not url:
            return {"error": None, "content": None}
        hit = await asyncio.to_thread(
            _find_prep_snapshot_sync, url, float(max(0, window_s))
        )
        return {"error": None, "content": hit}
    except Exception as exc:
        logger.exception("prep_store.find_prep_snapshot_by_source failed")
        return {"error": str(exc), "content": None}


async def list_unfinished_preps(limit: int = 3) -> dict:
    """Non-expired preps showing real activity (a fetched worker packet or
    >=1 staged category), newest first -- DISCLOSURE data for qa-doctor /
    qa_prepare_test_cases so an abandoned run stops evaporating silently
    (2026-07-31 incident). Read-only; never raises."""
    try:
        rows = await asyncio.to_thread(_list_unfinished_sync, int(limit))
        return {"error": None, "content": rows}
    except Exception as exc:
        logger.exception("prep_store.list_unfinished_preps failed")
        return {"error": str(exc), "content": None}
