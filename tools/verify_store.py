"""Atlassian connection-verification verdict store -- SQLite, never-raises.

``qa_configure_jira(atlassian_verify_json=...)`` turns the agent's read-only
``atlassianUserInfo`` result into a real verdict, and until 2026-09-01 threw it
away. ``qa-doctor`` then re-guessed from on-disk config on EVERY run and
re-raised the same "Fix now: verify the connection" item forever -- even one
minute after a successful probe -- and, because that item was unconditional, it
pinned the report's headline off "Ready" permanently (see ``overall_verdict``).

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
from pathlib import Path

import llm
import tools.jira_mcp as jira_mcp
from config.settings_guard import never_raise
from tools import settings_port, verify_store_db
from tools.install_paths import resolve_data_path
from tools.verify_store_db import Verdict

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
    client       TEXT PRIMARY KEY,
    verified     INTEGER NOT NULL,
    at_epoch     REAL NOT NULL,
    tool         TEXT NOT NULL,
    site_checked INTEGER NOT NULL DEFAULT 0
);
"""


def db_path() -> Path:
    # Anchored to the install root, not the process cwd -- same reasoning as
    # prep_store: a verdict recorded while one project was open must not be
    # invisible when another is.
    return resolve_data_path(settings_port.get().qa_suite_store_path)


def _connect() -> sqlite3.Connection:
    return verify_store_db.open_db(_db_path(), _SCHEMA, _BUSY_TIMEOUT_S)


def _run(fn, *args):
    conn = _connect()
    try:
        return fn(conn, *args)
    finally:
        conn.close()


def client_key() -> str:
    """The calling editor, or "" when the handshake did not name one.

    A failing ``llm`` call degrades to the shared "" row rather than break the store.
    """
    with never_raise(logger, "verify_store: host client unavailable", exc_info=True):
        return str(llm.get_host_client() or "")[:64]
    return ""


def _apply_provenance(rec: dict) -> None:
    rec["fresh"] = rec["age_s"] <= VERDICT_FRESH_S
    # Provenance is a SECOND, independent freshness axis: a verdict can
    # be minutes old and still not be evidence, because the probe that
    # produced it did not ask the question we now ask. Evaluated on READ
    # like `fresh`, so widening the accepted set re-judges existing rows
    # rather than stranding them. Defaults False on any failure -- that
    # re-asks one cheap read-only question, where True would certify
    # access nobody checked.
    with never_raise(
        logger, "verify_store: provenance check skipped", exc_info=True
    ) as g:
        rec["current_probe"] = rec["tool"] in jira_mcp.probe_provenance_accepted()
    if g.failed:
        rec["current_probe"] = False


async def record_verdict(verified: bool, tool: str, site_checked: bool = False) -> dict:
    """Persist ONLY {verified, at, tool, site_checked} for THIS client.

    ``site_checked`` is a BOOLEAN: whether the access half was actually checked,
    never WHICH site or what it was called. Never raises.
    """
    with never_raise(
        logger, "verify_store: recording the verdict failed", exc_info=True
    ) as g:
        client = client_key()
        v = Verdict(
            client=client,
            verified=bool(verified),
            tool=tool,
            at_epoch=time.time(),
            site_checked=bool(site_checked),
        )
        await asyncio.to_thread(_run, verify_store_db.save, v)
        return {"error": None, "content": True}
    return {"error": str(g.exc), "content": None}


async def load_verdict() -> dict:
    """The stored verdict for THIS client, or None. Never raises.

    content: ``{"verified": bool, "at": "<iso>", "tool": str, "age_s": float,
    "fresh": bool}`` or ``None`` when this client has never verified.

    Staleness is evaluated on READ, never written down, so changing
    ``VERDICT_FRESH_S`` re-judges existing rows instead of stranding them.
    """
    with never_raise(
        logger, "verify_store: loading the verdict failed", exc_info=True
    ) as g:
        client = client_key()
        rec = await asyncio.to_thread(_run, verify_store_db.load_row, client)
        if rec is not None:
            _apply_provenance(rec)
        return {"error": None, "content": rec}
    return {"error": str(g.exc), "content": None}


def _db_path() -> Path:
    """Legacy name of :func:`db_path` (WX removes it)."""
    return db_path()
