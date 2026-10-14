"""Updater manifest-to-tree binding for a release before it is swapped in (split from tools/updater.py)."""

from __future__ import annotations

import logging
from pathlib import Path

from config.settings_guard import never_raise
from tools.updater_keys import verify_manifest_signature
from tools.updater_log import logger
from tools.updater_manifest import load_manifest, verify_integrity
from tools.updater_paths import (
    _MANIFEST_NAME,
    _MANIFEST_SIG_NAME,
    _is_derived_artifact,
    _is_protected,
)

# Files apply_update would copy that are legitimately ABSENT from MANIFEST.sha256
# (a manifest cannot hash itself, and the detached signature is written after it).
# build_dist.build_manifest lists every OTHER non-protected file, so anything else
# missing from the manifest is an unlisted / smuggled file.
_MANIFEST_UNLISTED_OK = (_MANIFEST_NAME, _MANIFEST_SIG_NAME)


def _swap_candidate_files(new_tree: Path) -> list:
    """The non-protected files apply_update would actually copy out of ``new_tree``
    (mirrors apply_update's own selection). Never raises."""
    files: list = []
    try:
        for src in sorted(new_tree.rglob("*")):
            if not src.is_file():
                continue
            rel = src.relative_to(new_tree).as_posix()
            if _is_protected(rel) or _is_derived_artifact(rel):
                continue
            files.append(rel)
    except OSError as exc:
        logger.warning("Could not enumerate release tree (%s).", exc)
    return files


def _manifest_missing_ok(signed: bool) -> bool:
    """Binding for a tree with no manifest: only a legacy unsigned release proceeds."""
    if signed:
        logger.warning(
            "ABORTING: a signed release has no readable %s to bind the "
            "signature to its files -- refusing.",
            _MANIFEST_NAME,
        )
        return False
    # Legacy unsigned, manifest-less release: preserve current behavior.
    return True


def _warn_mismatch(mismatched: list) -> None:
    """Log the hash-mismatch abort."""
    logger.warning(
        "ABORTING: release tree does not match its %s (%d file(s) differ, "
        "e.g. %s) -- refusing to install unverified bytes.",
        _MANIFEST_NAME,
        len(mismatched),
        mismatched[:5],
    )


def _unlisted_files(new_tree: Path) -> list:
    """Swap files of ``new_tree`` that its manifest does not list."""
    listed = set(load_manifest(new_tree).keys())
    return [
        rel
        for rel in _swap_candidate_files(new_tree)
        if rel not in listed and rel not in _MANIFEST_UNLISTED_OK
    ]


def _warn_unlisted(unlisted: list) -> None:
    """Log the unlisted-file abort."""
    logger.warning(
        "ABORTING: release tree carries %d file(s) absent from %s "
        "(e.g. %s) -- refusing to install unlisted code.",
        len(unlisted),
        _MANIFEST_NAME,
        unlisted[:5],
    )


def _tree_matches_manifest(new_tree: Path) -> bool:
    """True when every listed file matches and no swap file is unlisted."""
    mismatched = verify_integrity(new_tree)
    if mismatched:
        _warn_mismatch(mismatched)
        return False
    unlisted = _unlisted_files(new_tree)
    if unlisted:
        _warn_unlisted(unlisted)
        return False
    return True


# Bind a release tree to its MANIFEST.sha256 before it is trusted for a swap.
#
# Guarantees a VALID signature implies the installed bytes match the signed
# manifest (closes the C1 signing-bypass: a genuine (manifest, sig) pair could
# otherwise ship alongside modified/added code). Two checks:
#
# * every manifest-listed file matches on disk (``verify_integrity == []``), and
# * every code file the swap would copy is LISTED in the manifest (no unlisted /
#   smuggled files, except the manifest + detached signature themselves).
#
# Policy by signature status:
#   * signed ("valid")    -> binding is MANDATORY; a signed tree with no readable
#     manifest, a mismatch, or an unlisted file is REJECTED.
#   * unsigned ("missing") -> apply the SAME strict binding ONLY when a
#     MANIFEST.sha256 is present (integrity is independent of the signature); a
#     manifest-less legacy release keeps today's behavior and proceeds.
#
# Returns True to PROCEED, False to ABORT. Never raises.
def _manifest_binding_ok(new_tree: Path) -> bool:
    """True to proceed with the swap of ``new_tree``, False to abort."""
    with never_raise(
        logger,
        "manifest-binding check failed -- refusing the swap (fail-closed).",
        level=logging.WARNING,
        exc_info=True,
    ):
        signed = verify_manifest_signature(new_tree) == "valid"
        if not (new_tree / _MANIFEST_NAME).is_file():
            return _manifest_missing_ok(signed)
        return _tree_matches_manifest(new_tree)
    return False
