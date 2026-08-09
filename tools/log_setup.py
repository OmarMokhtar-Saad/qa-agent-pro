"""Per-PROCESS log files, so concurrent servers cannot lose each other's lines.

2026-08-09, from live evidence in a tester's install: THREE MCP client processes
(Cursor, Claude Desktop, Claude Code) run against ONE install directory and, until
this module, all three appended to one ``data/logs/qa-agents.log`` through a
``RotatingFileHandler``. A 15:08 finalize wrote its audit events and its xlsx while
the file's substantive entries stopped at 15:04 -- the lines were simply gone, and
that sabotaged the day's diagnosis.

WHY THE SHARED FILE LOSES LINES. ``RotatingFileHandler`` is documented as
single-process. Across processes each one independently renames ``.log`` to
``.log.1`` at its own rollover and reopens the name, while its peers keep writing
to file objects that now point at the rotated-away (or, one rollover later,
unlinked) inode. Everything those peers wrote after that moment is either invisible
to anyone tailing the live name or deleted outright when ``backupCount`` shifts.

PER-LINE ATOMICITY IS NOT THE FIX. An ``O_APPEND`` write is atomic with respect to
the file it is open ON, not to the file NAME, so rotation defeats it regardless of
how small the write is. The standard fix is the one implemented here: give every
process its OWN file, keyed by pid, and bound the directory with a retention sweep.
Rotation is KEPT -- with a per-pid name each file has exactly one writer, which is
the configuration ``RotatingFileHandler`` actually supports.

ATTRIBUTION. Every line carries ``[pid=... client=...]``. The pid names the writer
even after a file is copied out of the directory; the client name (from the MCP
``initialize`` handshake, forwarded by ``mcp_server._note_client``) names WHICH
editor it was, which is the question a diagnoser on a three-client install actually
has. The client reads ``unknown`` until the first tool call, by construction: the
handshake has not happened when logging is configured.

PID REUSE. An OS recycles pids, so an old file can carry the number of a live
process. That costs nothing: the new process simply APPENDS to it (append never
loses bytes), the two generations are told apart by the timestamp and the client
tag on every line, and the sweep's age rule retires the old bytes. It is also why
the sweep refuses to delete a file whose pid is ALIVE -- see ``_pid_is_alive``.

stdlib only, never raises (a logging failure must never take a server down), and no
``llm.*`` call. macOS and Windows both: a pid in a filename, no ``fcntl``, no
locking, no symlinks.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

logger = logging.getLogger("qa_agents.log_setup")

DEFAULT_PREFIX = "qa-agents"
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 3
UNKNOWN_CLIENT = "unknown"
_MAX_CLIENT_CHARS = 40
LOG_FORMAT = (
    "%(asctime)s %(levelname)s %(name)s "
    "[pid=%(process)d client=%(qa_client)s]: %(message)s"
)

# tools/log_setup.py -> tools/ -> the install root.
_INSTALL_ROOT = Path(__file__).resolve().parent.parent
_env_cache: dict[str, str] = {}


def _env_file_values() -> dict[str, str]:
    """The install's .env as a flat dict. Cached, capped, never raises.

    ``config/settings`` parses .env into pydantic, NOT into ``os.environ``, and this
    module must stay stdlib-only and import-cheap (the dist launcher imports it
    before ``tools/updater`` may swap the tree, and importing settings from there
    would be a new startup dependency). So the three retention knobs are read here:
    ``os.environ`` first, then a lenient scan of the install's own .env."""
    if _env_cache:
        return _env_cache
    _env_cache["__loaded__"] = "1"
    try:
        env_path = _INSTALL_ROOT / ".env"
        if not env_path.is_file():
            return _env_cache
        with env_path.open("r", encoding="utf-8", errors="replace") as handle:
            raw = handle.read(1024 * 1024)
        for line in raw.splitlines():
            text = line.strip()
            if not text or text.startswith("#") or "=" not in text:
                continue
            key, _, value = text.partition("=")
            name = key.strip().upper()
            # 2026-08-09 (review L1): cache ONLY the knobs this module reads. The
            # scan used to keep every .env value -- including five API secrets --
            # resident for the whole process lifetime in order to answer three
            # QA_LOG_* lookups.
            if not name.startswith("QA_LOG_"):
                continue
            _env_cache[name] = value.split("#")[0].strip().strip("\"'")
    except Exception:
        logger.debug("could not read .env for the log settings", exc_info=True)
    return _env_cache


def _setting(name: str) -> str:
    try:
        value = os.environ.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
        return _env_file_values().get(name, "")
    except Exception:  # pragma: no cover - defensive
        return ""


def _bool_setting(name: str, default: bool) -> bool:
    raw = _setting(name).lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


def _int_setting(name: str, default: int, minimum: int) -> int:
    try:
        raw = _setting(name)
        return max(minimum, int(float(raw))) if raw else default
    except Exception:
        logger.debug("unusable %s -- using %s", name, default, exc_info=True)
        return default


def _float_setting(name: str, default: float, minimum: float) -> float:
    try:
        raw = _setting(name)
        return max(minimum, float(raw)) if raw else default
    except Exception:
        logger.debug("unusable %s -- using %s", name, default, exc_info=True)
        return default


# Retention. A file goes when it is older than RETENTION_DAYS *or* beyond the newest
# RETENTION_MAX_FILES. Deleting logs is destructive -- the file swept could be the
# one a diagnoser wanted -- so an operator can widen or disable it without touching
# code, while the DEFAULT stays on: this is the bug fix, and an unbounded directory
# on a multi-client install is its own incident. Every knob is lenient and
# never-raising, exactly like config/settings' coercers.
RETENTION_ENABLED = _bool_setting("QA_LOG_RETENTION_ENABLED", True)
RETENTION_DAYS = _float_setting("QA_LOG_RETENTION_DAYS", 14.0, 1.0)
RETENTION_MAX_FILES = _int_setting("QA_LOG_RETENTION_MAX_FILES", 40, 2)

_client = {"name": UNKNOWN_CLIENT}


def set_client(name: object, log_dir: str | Path | None = None) -> str:
    """Record the MCP client that owns this process, for the per-line tag.

    Called from ``mcp_server._note_client`` with the handshake's
    ``clientInfo.name``. Sanitised hard: the value is host-supplied and ends up in
    every log line, where a newline would forge a log entry. Never raises."""
    try:
        raw = str(name or "").strip().lower()
        cleaned = "".join(ch for ch in raw if ch.isalnum() or ch in "-_.")
        resolved = cleaned[:_MAX_CLIENT_CHARS] or UNKNOWN_CLIENT
        changed = resolved != _client.get("name")
        _client["name"] = resolved
        if changed and resolved != UNKNOWN_CLIENT:
            # One greppable client -> file mapping. Without it a diagnoser has to
            # read INTO each file to find out which editor owns which pid.
            logger.info(
                "client=%s owns %s",
                resolved,
                process_log_path(log_dir) if log_dir else f"pid {os.getpid()}",
            )
    except Exception:
        logger.debug("could not record the MCP client name", exc_info=True)
        _client["name"] = UNKNOWN_CLIENT
    return _client["name"]


def current_client() -> str:
    """The client tag for this process (``unknown`` before the handshake)."""
    try:
        return _client.get("name") or UNKNOWN_CLIENT
    except Exception:  # pragma: no cover - a dict read cannot realistically fail
        return UNKNOWN_CLIENT


class ProcessContextFilter(logging.Filter):
    """Attach ``qa_client`` to every record.

    The pid needs no filter: ``process`` is a standard LogRecord attribute."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.qa_client = current_client()
        except Exception:  # pragma: no cover - defensive
            record.qa_client = UNKNOWN_CLIENT
        return True


def process_log_path(
    log_dir: str | Path,
    prefix: str = DEFAULT_PREFIX,
    pid: int | None = None,
) -> Path:
    """This process's own log file: ``<prefix>-<pid>.log``.

    *pid* is injectable so tests can model two processes without forking."""
    return Path(log_dir) / f"{prefix}-{os.getpid() if pid is None else pid}.log"


def _pid_from_name(name: str, prefix: str) -> int | None:
    """The pid encoded in ``<prefix>-<pid>.log[.N]``, or None if it does not parse."""
    try:
        stem = name[len(prefix) + 1 :].split(".", 1)[0]
        return int(stem)
    except Exception:
        return None


# 2026-08-09 (review L2): off POSIX _pid_is_alive is ALWAYS False, so every
# peer's file looks dead and a startup sweep past RETENTION_MAX_FILES could
# delete a LIVE peer's already-rotated .log.1 / .log.2 -- Windows' open-file
# protection covers only the file the peer currently has open. A backup touched
# inside this window almost certainly belongs to a running process, so it is
# skipped. POSIX is unaffected: there the pid check is authoritative.
_NON_POSIX_GRACE_S = 3600.0


def _off_posix() -> bool:
    """Is this a non-POSIX platform? Read through ONE helper (review W5) so a
    test can exercise the Windows branch by patching THIS module, instead of
    monkeypatching the stdlib's ``os.name`` process-wide. Never raises."""
    try:
        return os.name != "posix"
    except Exception:  # pragma: no cover - defensive
        return True


def _pid_is_alive(pid: int | None) -> bool:
    """Is a process with this pid running? Unknown counts as ALIVE (keep the file).

    WHY THIS EXISTS: on POSIX, unlinking a file another process still has OPEN
    succeeds and silently strands every subsequent line of that peer in an
    unreachable inode -- a miniature recurrence of the very bug this module fixes.
    A peer that has simply been idle longer than the age window is the realistic
    case on a three-client install.

    POSIX only, deliberately: on Windows ``os.kill(pid, 0)`` calls TerminateProcess
    and would KILL the peer, so this returns False there and the protection is the
    OS's own -- Windows refuses to unlink an open file, and that failure is already
    swallowed by the guarded unlink in ``sweep_old_logs``.
    """
    if pid is None:
        return True
    if _off_posix():
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # it exists, it is just owned by another user
    except Exception:
        return True  # no os.kill, a silly pid, anything: keep the file


def sweep_old_logs(
    log_dir: str | Path,
    *,
    prefix: str = DEFAULT_PREFIX,
    keep: int | None = None,
    days: float | None = None,
    now: float | None = None,
    skip: str | Path | None = None,
    enabled: bool | None = None,
) -> list[str]:
    """Delete this prefix's stale per-process files; return the names removed.

    Per-process files would otherwise accumulate one per client per restart. A file
    is removed when it is older than *days* OR beyond the newest *keep* by mtime.

    Four guarantees this function must keep, because it DELETES:
    * a file whose pid is still ALIVE is never removed (see ``_pid_is_alive``);
    * the active process's file (and its rotated backups) is never a candidate --
      pass it as *skip*;
    * a file that cannot be deleted (locked on Windows, owned by another user,
      already gone) is skipped, never raised on -- retention must never take down
      a server or a supervisor;
    * the legacy SHARED ``qa-agents.log`` / ``launcher.log`` cannot match the glob
      (``<prefix>-*``), so pre-existing evidence is left exactly where it is.
    """
    removed: list[str] = []
    try:
        if not (RETENTION_ENABLED if enabled is None else enabled):
            return removed
        keep_n = RETENTION_MAX_FILES if keep is None else int(keep)
        max_age = RETENTION_DAYS if days is None else float(days)
        now_ts = time.time() if now is None else float(now)
        cutoff = now_ts - max_age * 86400.0
        active = Path(skip).name if skip else ""
        entries: list[tuple[float, Path]] = []
        for path in Path(log_dir).glob(f"{prefix}-*.log*"):
            try:
                if active and path.name.startswith(active):
                    continue
                if _pid_is_alive(_pid_from_name(path.name, prefix)):
                    continue
                mtime = path.stat().st_mtime
                # See _NON_POSIX_GRACE_S: off POSIX the pid check cannot help.
                if _off_posix() and (now_ts - mtime) < _NON_POSIX_GRACE_S:
                    continue
                entries.append((mtime, path))
            except OSError:
                continue
        entries.sort(key=lambda item: item[0], reverse=True)
        for index, (mtime, path) in enumerate(entries):
            if index < keep_n and mtime >= cutoff:
                continue
            try:
                path.unlink()
                removed.append(path.name)
            except OSError:
                logger.debug("could not delete the old log %s", path, exc_info=True)
    except Exception:
        logger.debug("the log retention sweep failed", exc_info=True)
    return removed


def configure_file_logging(
    log_dir: str | Path,
    *,
    prefix: str = DEFAULT_PREFIX,
    level: int = logging.INFO,
    max_bytes: int | None = None,
    backup_count: int | None = None,
    pid: int | None = None,
    target: logging.Logger | None = None,
) -> logging.Handler | None:
    """Attach this process's OWN rotating file handler and sweep old files.

    Returns the handler, or ``None`` when no file could be opened -- the caller
    degrades to INFO-on-stderr rather than losing the trail. Calling it twice for
    the same path returns the handler already attached, so a re-entrant startup
    cannot double every line. Never raises.

    *max_bytes* / *backup_count* are explicit so each caller keeps its own budget
    (the dist launcher stays at 1 MiB x 2, the server at 5 MiB x 3). *target*
    defaults to the root logger; tests pass their own so two simulated processes do
    not share one record stream."""
    from logging.handlers import RotatingFileHandler

    try:
        directory = Path(log_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = process_log_path(directory, prefix=prefix, pid=pid)
        owner = target if target is not None else logging.getLogger()
        for existing in list(owner.handlers):
            if getattr(existing, "qa_log_path", "") == str(path):
                return existing
        handler = RotatingFileHandler(
            str(path),
            maxBytes=MAX_BYTES if max_bytes is None else int(max_bytes),
            backupCount=BACKUP_COUNT if backup_count is None else int(backup_count),
            encoding="utf-8",
        )
        handler.setLevel(level)
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        handler.addFilter(ProcessContextFilter())
        handler.qa_log_path = str(path)
        owner.addHandler(handler)
        sweep_old_logs(directory, prefix=prefix, skip=path)
        return handler
    except Exception:
        logger.debug("could not open a per-process log file", exc_info=True)
        return None
