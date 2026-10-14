"""Register this MCP server in an editor's own MCP config, safely.

ONE implementation, used by BOTH ``connect.sh`` (the interactive installer step)
and the launcher's startup pass, so the two cannot drift. Before this, the merge
logic lived only inside the ``CONNECT_SH`` template in ``scripts/build_dist.py``
as an inline Python heredoc, which meant a second caller had to copy it.

WHY A STARTUP PASS EXISTS AT ALL: registration used to happen exactly once, during
install. ``connect.sh`` skips a client whose config directory does not exist yet,
``install.sh`` refuses to re-run without ``QA_FORCE``, and the launcher never
called ``connect.sh`` -- so an editor installed AFTER qa-agent-pro was never picked
up, and the tester had no way to know that ``connect.sh`` needed re-running.

HOUSE RULES THIS MODULE OBEYS:

* **stdlib only.** The launcher deliberately imports nothing internal except
  ``tools.updater`` (and the stdlib-only leaves it uses) so that a self-update
  can swap code before it is loaded. This module therefore imports no project
  module either (bar the stdlib-only leaf ``tools.process_env``), and takes the
  command and the target paths as arguments rather than reading settings itself.
* **insert-only is a CHOICE, not the default behaviour of one function.**
  ``connect.sh`` deliberately REPAIRS a stale entry (the install path may have
  moved), while the startup pass must never rewrite an entry a tester hand-tuned.
  Same code, explicit ``insert_only`` flag, so neither caller can inherit the
  other's semantics by accident.
* **Atomic and locked.** Three editors share one install here, so a startup pass
  can mean concurrent read-modify-write on the same file. Writes go to a temp file
  in the same directory and are ``os.replace``d, under an advisory lock.
* **``.bak`` only when a write actually happens.** The old code copied the backup
  before every write attempt, including no-ops, so a re-run could overwrite a
  good backup with identical content and a crash mid-write left no earlier copy.
* **Malformed config is never clobbered.** Unparseable JSON returns an error and
  writes nothing: losing a tester's other MCP servers is far worse than not
  registering.

Advisory locking is POSIX only (``fcntl``) and simply degrades on Windows; the
CONFIG PATHS are cross-platform, so native-Windows installs register the same
way. What remains Windows-specific is only the lock -- a narrow gap, not
an oversight.
"""

from __future__ import annotations

import json  # noqa: F401
import logging
import os  # noqa: F401
import sys  # noqa: F401
import tempfile  # noqa: F401
from pathlib import Path  # noqa: F401

from tools.clireg_all import register_all, register_atlassian  # noqa: F401
from tools.clireg_consts import (  # noqa: F401
    _RECOVERABLE,
    ADDED,
    ATLASSIAN_ENTRY,
    ATLASSIAN_NAME,
    ERROR,
    PRESENT,
    SERVER_NAME,
    SKIPPED,
    UPDATED,
)
from tools.clireg_lock import _atomic_write, _lock, _unlock  # noqa: F401
from tools.clireg_scan import (  # noqa: F401
    _looks_like_qa_server,
    _scan_registrations,
    discover_registrations,
    install_target,
)
from tools.clireg_spec import ClientOpts, EntrySpec  # noqa: F401
from tools.clireg_targets import (  # noqa: F401
    _PROJECT_CONFIG_RELPATHS,
    atlassian_targets,
    default_targets,
)
from tools.clireg_warn import (  # noqa: F401
    _SERVER_ROOT,
    root_is_relative,
    split_server_warning,
    workspace_contains_server_warning,
)
from tools.clireg_write import (  # noqa: F401
    _place_entry,
    _read_config,
    _register_locked,
    register_client,
    register_entry,
)

logger = logging.getLogger("qa_agents.client_registry")
