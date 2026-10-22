"""Updater protected paths, derived-artifact rule and release file names (split from tools/updater.py)."""

from __future__ import annotations

# Operator-local state a code update must NEVER overwrite. Entries are POSIX
# paths relative to the install root; a path is protected if it equals one of
# these OR is nested under one (prefix match on a path boundary). Everything
# else in the new release tree is treated as replaceable source code.
PROTECTED_PATHS = (
    ".env",  # secrets
    "data",  # SQLite suite store + audit log (*.db)
    "corpus",  # RAG corpus JSONL files
    "maestro_flows",  # generated, re-exportable device flows
    ".claude",  # local settings/plans/reports/hooks state
    ".git",  # version control
    ".venv",  # virtualenv
    "backups",  # prior update backups
    "__pycache__",  # compiled artifacts
)


def _is_protected(rel_posix: str) -> bool:
    """True if a repo-relative POSIX path is operator-local state (never swap)."""
    for p in PROTECTED_PATHS:
        if rel_posix == p or rel_posix.startswith(p + "/"):
            return True
    return False


def _is_derived_artifact(rel_posix: str) -> bool:
    """True for build-derived Python bytecode: any ``*.pyc`` file OR any path with
    a ``__pycache__`` component at ANY depth (``_is_protected`` only guards the
    top-level one). These are legitimately excluded from ``MANIFEST.sha256`` by
    ``build_dist.build_manifest``, so the manifest binding must IGNORE them.

    CRITICAL: this predicate is the SINGLE source both ``_swap_candidate_files``
    (so the binding ignores derived bytecode) and ``apply_update`` (so it is never
    copied into the install) route through -- the two skip sets MUST stay
    identical. Ignoring ``.pyc`` in the binding alone would let an attacker plant
    a malicious ``evil.pyc`` / ``__pycache__/mod.cpython-312.pyc`` (shadowing a
    source module) in a release asset that apply_update would then install and
    import, bypassing the manifest. Kept separate from ``_is_protected``
    (operator-local state) on purpose. Pure string ops -- never raises."""
    if rel_posix.endswith(".pyc"):
        return True
    return "__pycache__" in rel_posix.split("/")


_MANIFEST_NAME = "MANIFEST.sha256"

_MANIFEST_SIG_NAME = "MANIFEST.sig"

# Locked read-only in addition to every manifest entry (release metadata + the
# launcher itself, so "python launcher.py" cannot be edited out from under us).
_LOCK_EXTRA = ("MANIFEST.sha256", "MANIFEST.sig", "launcher.py", "VERSION")

# Files apply_update must NEVER prune, even when a release drops them from its
# tree: release metadata plus the launcher the client spawns (`python
# launcher.py` must keep working across a bad release). Operator state -- .env,
# data/, corpus/, local editor config -- is already covered by _is_protected.
_NEVER_PRUNE = frozenset(_LOCK_EXTRA) | {".env.example"}

#: Name of the release-shipped list of paths a PAST release installed and a
#: LATER release deleted. Hashed into ``MANIFEST.sha256`` and covered by
#: ``MANIFEST.sig`` like any other shipped file, so it cannot be edited on a
#: client without failing the integrity gates.
_REMOVED_PATHS_NAME = "REMOVED_PATHS"
