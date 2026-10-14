"""GitHub-Release startup self-update (``QA_AUTO_UPDATE_ENABLED`` — opt-in, OFF by default).

House rule: this module imports **nothing internal except ``config.settings``
and the stdlib-only leaves ``tools.http_port`` / ``tools.process_runner``**
(the launcher path must not pull in ``mcp_server.py`` or any agent). It is
invoked by ``launcher.py`` *before* the server imports load, so an update can
swap code in place first.

Contract — **never raises to the launcher**: :func:`run_update_check` wraps its
whole body in ``try/except`` and returns a short status string. Any failure
(GitHub unreachable, bad token, malformed version, mid-swap error) is logged at
``WARNING`` and the CURRENT version is started anyway. The update check must
never block or crash startup.

Data safety: :data:`PROTECTED_PATHS` enumerates operator-local state that the
code swap must never overwrite (``.env``, SQLite stores, RAG corpus, auth users,
local ``.claude`` files, the venv, git, and prior backups). The swap keeps a
timestamped backup under ``backups/`` and rolls back on any error.

Secrets: ``GITHUB_TOKEN`` is sent only as an ``Authorization: Bearer`` header
when set, and is never logged.

Integrity + read-only lock (``QA_CODE_LOCK_ENABLED`` — opt-in, OFF by default;
the distribution launcher forces it on): releases ship a ``MANIFEST.sha256``
listing every code file's hash. On startup, when the install is already up to
date, any file whose hash differs from the manifest is healed by re-applying
the current release zipball, and every manifest-listed file is then chmod'ed
read-only so manual/editor edits fail to save and never survive a restart.
"""

from __future__ import annotations

import logging  # noqa: F401
import os  # noqa: F401
import shutil  # noqa: F401

import httpx  # noqa: F401

from tools.updater_binding import (  # noqa: F401
    _MANIFEST_UNLISTED_OK,
    _manifest_binding_ok,
    _swap_candidate_files,
    _tree_matches_manifest,
)
from tools.updater_check import (  # noqa: F401
    _INSTALL_DIR,
    _check_release,
    _http_error_status,
    _update_to_newer,
    run_update_check,
)
from tools.updater_env import (  # noqa: F401
    _PIP_TIMEOUT,
    _commented_env_key,
    _env_line_key,
    _pip_install,
    migrate_env,
)
from tools.updater_gate import (  # noqa: F401
    _signature_gate_ok,
)
from tools.updater_github import (  # noqa: F401
    _GITHUB_API,
    _RATE_LIMIT_STATUSES,
    _RELEASE_CACHE_TTL_S,
    _auth_headers,
    _fetch_latest_release_uncached,
    _rate_limit_detail,
    _release_cache,
    _release_cache_lock,
    fetch_latest_release,
)
from tools.updater_heal import (  # noqa: F401
    _HEAL_MARKER_MAX_BYTES,
    MANIFEST_HEAL_RETRY_INTERVAL_S,
    _heal_marker,
    _healed_outcome,
    _manifest_heal_deferred,
    _read_heal_marker,
    _write_heal_marker,
)
from tools.updater_keys import (  # noqa: F401
    _RELEASE_PUBLIC_KEYS_HEX,
    _any_key_verifies,
    _check_embedded_pubkey,
    _release_keys,
    verify_manifest_signature,
)
from tools.updater_lock import (  # noqa: F401
    _UPDATE_LOCK_FILE,
    _open_lock_fd,
    _release_lock_fd,
    _stamp_lock,
    _update_lock,
)
from tools.updater_lockdiag import (  # noqa: F401
    HELD_TOO_LONG_S,
    _held_too_long,
    _lock_holder_note,
    _log_lock_contention,
    _warn_lock_is_dir,
)
from tools.updater_log import (  # noqa: F401
    logger,
)
from tools.updater_manifest import (  # noqa: F401
    _INTEGRITY_MAX_WORKERS,
    _file_sha256,
    integrity_problems,
    load_manifest,
    lock_files,
    verify_integrity,
)
from tools.updater_oslock import (  # noqa: F401
    LockUnsupported,
    _os_lock,
    _os_unlock,
    _try_os_lock,
)
from tools.updater_overlay import (  # noqa: F401
    _make_writable,
    _overlay_files,
    _plan_release_files,
    _prunable_for_tree,
    _prune_files,
)
from tools.updater_paths import (  # noqa: F401
    _LOCK_EXTRA,
    _MANIFEST_NAME,
    _MANIFEST_SIG_NAME,
    _NEVER_PRUNE,
    _REMOVED_PATHS_NAME,
    PROTECTED_PATHS,
    _is_derived_artifact,
    _is_protected,
)
from tools.updater_prune import (  # noqa: F401
    _prunable,
    load_removed_paths,
)
from tools.updater_release import (  # noqa: F401
    _download_verify_apply,
    _heal_from_release,
    apply_update_locked,
)
from tools.updater_selfheal import (  # noqa: F401
    _lock_and_heal,
    _relock_and_log,
)
from tools.updater_swap import (  # noqa: F401
    _rollback,
    apply_update,
)
from tools.updater_version import (  # noqa: F401
    _local_version,
    _parse_version,
    is_newer,
)
from tools.updater_zip import (  # noqa: F401
    _restore_exec_bits,
    _safe_extract_zip,
    download_and_extract,
)
