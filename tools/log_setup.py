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

stdlib only (plus the stdlib-only leaf ``tools.process_env``), never raises
(a logging failure must never take a server down), and no
``llm.*`` call. macOS and Windows both: a pid in a filename, no ``fcntl``, no
locking, no symlinks.
"""

from __future__ import annotations

import logging  # noqa: F401
import os  # noqa: F401
import time  # noqa: F401
from dataclasses import dataclass  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import Any, cast  # noqa: F401

from tools.log_setup_base import (  # noqa: F401
    _MAX_CLIENT_CHARS,
    BACKUP_COUNT,
    DEFAULT_PREFIX,
    LOG_FORMAT,
    MAX_BYTES,
    UNKNOWN_CLIENT,
    _pid_from_name,
    logger,
    process_log_path,
)
from tools.log_setup_client import (  # noqa: F401
    ProcessContextFilter,
    _client,
    current_client,
    set_client,
)
from tools.log_setup_config import (  # noqa: F401
    FileLoggingOptions,
    configure_file_logging,
)
from tools.log_setup_env import (  # noqa: F401
    _INSTALL_ROOT,
    RETENTION_DAYS,
    RETENTION_ENABLED,
    RETENTION_MAX_FILES,
    _bool_setting,
    _env_cache,
    _env_file_values,
    _float_setting,
    _int_setting,
    _setting,
)
from tools.log_setup_sweep import (  # noqa: F401
    _NON_POSIX_GRACE_S,
    SweepOptions,
    _delete_stale,
    _off_posix,
    _pid_is_alive,
    _sweep_candidates,
    sweep_old_logs,
)
from tools.process_env import get_env  # noqa: F401
