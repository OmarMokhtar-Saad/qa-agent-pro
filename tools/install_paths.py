"""Resolve a configured RELATIVE data path against the INSTALL ROOT.

2026-08-03. Three data-store settings default to RELATIVE paths and were used
as-is:

    qa_suite_store_path = "data/suites.db"   suites, cases, preps, submissions
    qa_audit_log_path   = "data/audit.db"    the audit trail
    qa_rag_storage_path = "corpus"           the RAG corpus

A relative path resolves against the PROCESS WORKING DIRECTORY, and an MCP client
chooses that directory when it spawns the server -- usually the folder the tester
happens to have open. So ONE install silently keeps several sets of data, and
which one you get depends on which project was open when the server started.

Observed consequences, all from real runs:

* a prep created under one working directory is "not found" under another, so the
  host is told to start again and re-runs a whole generation;
* `qa_export_suite <suite_id>` cannot find a suite that plainly exists;
* the audit trail splits across files, so "what happened" needs archaeology in
  more than one database;
* the RAG corpus splits, so "learn from past suites" silently sees a fraction of
  the history -- and one copy grew to 5033 entries against a 5000-entry cap while
  another held 178, which made every corpus write ~80x more expensive (112 ms vs
  1.4 ms) because at the cap each write prunes by rewriting the whole file.

``QA_EXPORT_DIR`` was fixed for exactly this class earlier the same day
(``mcp_handlers._resolved_export_dir``): "one install printed a different path per
client and a tester could not reliably find their own file". The three data stores
were left behind, and they matter more -- a misplaced export is confusing, while a
misplaced database silently fragments state.

This module is the ONE resolver, kept tiny and stdlib-only so every store module
can import it with no cycle risk.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("qa_agents.install_paths")

# tools/install_paths.py -> tools/ -> the install root.
INSTALL_ROOT = Path(__file__).resolve().parent.parent

# Remember which legacy locations we have already reported, so the disclosure is
# one line per path per process rather than one per database call.
_reported: set = set()


def resolve_data_path(value: str | Path) -> Path:
    """A configured data path as an ABSOLUTE path anchored to the install root.

    An ABSOLUTE value is returned unchanged (with ``~`` expanded), so an operator
    who pinned a path keeps it. A RELATIVE value is anchored to ``INSTALL_ROOT``.

    An EMPTY value yields ``Path("")``, which IS ``Path(".")`` -- it cannot round-trip
    as "" through a Path. That is deliberate and matches the pre-fix behaviour
    exactly (every caller here already did ``Path(setting)``), and none of the three
    data-store settings has a meaningful empty value: a database or corpus path of
    "" was broken before this function existed. The one setting that DOES treat ""
    specially -- ``qa_export_dir`` and its legacy secure-temp mode -- uses
    ``mcp_handlers._resolved_export_dir`` instead, which returns a str and keeps "".

    Never raises: on any failure the caller gets the original value, which is
    exactly the pre-fix behaviour.
    """
    try:
        raw = str(value or "").strip()
        if not raw:
            return Path(raw)
        path = Path(raw).expanduser()
        if path.is_absolute():
            return path
        resolved = INSTALL_ROOT / path
        _disclose_legacy(path, resolved)
        return resolved
    except Exception:
        logger.debug("resolving %r failed -- using it as-is", value, exc_info=True)
        return Path(str(value or ""))


def _disclose_legacy(relative: Path, resolved: Path) -> None:
    """Say so when data exists at the OLD cwd-relative location.

    Without this the fix would look like data loss: a tester whose history had been
    accumulating under some project folder would open the tool and find an empty
    corpus or a missing suite, with nothing on screen explaining why. One INFO line
    naming the old path is the difference between "my suites vanished" and "my
    suites are over there". Never raises, and never touches either file.
    """
    try:
        legacy = Path.cwd() / relative
        if legacy == resolved or not legacy.exists():
            return
        key = str(legacy)
        if key in _reported:
            return
        _reported.add(key)
        logger.info(
            "data path %s now resolves to %s (install root). An older copy exists "
            "at %s from when this path followed the working directory -- it is NOT "
            "read any more and nothing was moved or deleted.",
            str(relative),
            str(resolved),
            key,
        )
    except Exception:
        logger.debug("legacy-path disclosure failed", exc_info=True)
