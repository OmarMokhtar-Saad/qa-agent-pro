"""Atlassian connection-verification verdict store -- SQLite, never-raises.

``qa_configure_jira(atlassian_verify_json=...)`` turns the agent's read-only
``atlassianUserInfo`` result into a real verdict, and until 2026-09-01 threw it
away. ``qa-doctor`` then re-guessed from on-disk config on EVERY run and
re-raised the same "Fix now: verify the connection" item forever -- even one
minute after a successful probe -- and, because that item was unconditional, it
pinned the report's headline off "Ready" permanently (see ``_overall_verdict``).

WHAT IS PERSISTED, exhaustively: whether the probe succeeded, when, and the NAME
of the probe tool. NOTHING derived from the payload -- no account id, no email,
no display name, no error text, no fragment of the raw JSON. ``qa_configure_jira``
promises the tester that their identity is not kept, and that promise is about
IDENTITY; recording that a check happened, and its yes/no, keeps it.

Rows are keyed by the CALLING CLIENT (``llm.get_host_client()``): the OAuth
connection belongs to the editor, so a verdict from Cursor must not silence the
action item in Claude Desktop.

Same database file as prep_store/suite_store (``qa_suite_store_path``), its own
table, its own never-raise contract:

  On success: ``{"error": None, "content": <value>}``
  On failure: ``{"error": str, "content": None}``
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from config.settings import settings
from tools.install_paths import resolve_data_path

logger = logging.getLogger(__name__)

# How long a recorded verdict stays EVIDENCE. Deliberately a CONSTANT, not a
# setting: per the CLAUDE.md flag policy a new field needs one of four
# categories and this fits none -- it has no effect outside this process, needs
# no per-install configuration, is not an experiment, and there is no install
# where a different window is legitimate in BOTH directions.
#
# 7 days. An Atlassian OAuth grant outlives a working day but not a work-week's
# worth of token rotation, revocation, or a tester switching Atlassian sites,
# and a re-probe costs exactly ONE read-only tool call -- so a week-old "yes" is
# a reason to re-check, never a reason to trust.
VERDICT_FRESH_S = 7 * 24 * 3600

_BUSY_TIMEOUT_S = 5.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS atlassian_verifications (
    client    TEXT PRIMARY KEY,
    verified  INTEGER NOT NULL,
    at_epoch  REAL NOT NULL,
    tool      TEXT NOT NULL
);
"""


def _db_path() -> Path:
    # Anchored to the install root, not the process cwd -- same reasoning as
    # prep_store: a verdict recorded while one project was open must not be
    # invisible when another is.
    return resolve_data_path(settings.qa_suite_store_path)


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=_BUSY_TIMEOUT_S)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute(f"PRAGMA busy_timeout = {int(_BUSY_TIMEOUT_S * 1000)}")
    except sqlite3.Error:
        logger.debug(
            "verify_store: WAL/busy_timeout pragmas unavailable", exc_info=True
        )
    conn.executescript(_SCHEMA)
    return conn


def _client_key() -> str:
    """The calling editor, or "" when the handshake did not name one.

    Imported lazily: this module is itself imported from never-raising guards,
    so a broken ``llm`` import must degrade to the shared "" row rather than
    break the store.
    """
    try:
        import llm

        return str(llm.get_host_client() or "")[:64]
    except Exception:
        logger.debug("verify_store: host client unavailable", exc_info=True)
        return ""


def _iso(epoch: float) -> str:
    try:
        return (
            datetime.fromtimestamp(float(epoch), tz=timezone.utc)
            .replace(microsecond=0)
            .isoformat()
        )
    except Exception:
        return ""


def _save_sync(client: str, verified: bool, tool: str, at_epoch: float) -> None:
    conn = _connect()
    try:
        with conn:
            conn.execute(
                "INSERT INTO atlassian_verifications "
                "(client, verified, at_epoch, tool) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(client) DO UPDATE SET "
                "verified=excluded.verified, at_epoch=excluded.at_epoch, "
                "tool=excluded.tool",
                (client, 1 if verified else 0, float(at_epoch), str(tool)[:120]),
            )
    finally:
        conn.close()


def _load_sync(client: str) -> dict | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT verified, at_epoch, tool FROM atlassian_verifications "
            "WHERE client = ?",
            (client,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return {
        "verified": bool(row[0]),
        "at": _iso(row[1]),
        "tool": str(row[2] or ""),
        "age_s": max(0.0, time.time() - float(row[1])),
    }


async def record_verdict(verified: bool, tool: str) -> dict:
    """Persist ONLY {verified, at, tool} for THIS client. Never raises."""
    try:
        client = _client_key()
        await asyncio.to_thread(_save_sync, client, bool(verified), tool, time.time())
        return {"error": None, "content": True}
    except Exception as exc:
        logger.debug("verify_store: recording the verdict failed", exc_info=True)
        return {"error": str(exc), "content": None}


async def load_verdict() -> dict:
    """The stored verdict for THIS client, or None. Never raises.

    content: ``{"verified": bool, "at": "<iso>", "tool": str, "age_s": float,
    "fresh": bool}`` or ``None`` when this client has never verified.

    Staleness is evaluated on READ, never written down, so changing
    ``VERDICT_FRESH_S`` re-judges existing rows instead of stranding them.
    """
    try:
        client = _client_key()
        rec = await asyncio.to_thread(_load_sync, client)
        if rec is not None:
            rec["fresh"] = rec["age_s"] <= VERDICT_FRESH_S
        return {"error": None, "content": rec}
    except Exception as exc:
        logger.debug("verify_store: loading the verdict failed", exc_info=True)
        return {"error": str(exc), "content": None}
