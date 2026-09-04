"""GitHub-Release startup self-update (``QA_AUTO_UPDATE_ENABLED`` — opt-in, OFF by default).

House rule: this module imports **nothing internal except ``config.settings``**
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

import base64
import contextlib
import hashlib
import logging
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx

from config.settings import settings

logger = logging.getLogger("qa_agents.updater")

_GITHUB_API = "https://api.github.com"
# tools/updater.py -> repo root is two parents up.
_INSTALL_DIR = Path(__file__).resolve().parent.parent
# Generous cap for the post-update dependency install (network + build).
_PIP_TIMEOUT = 600

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

# Ed25519 release-signing PUBLIC key (hex, 32 bytes). The matching PRIVATE key
# is held ONLY by the release maintainer and never lives in this repo. Filled
# in once a keypair is generated with `python scripts/build_dist.py
# --generate-signing-key` (paste the printed hex here). Empty => no embedded
# key => signature verification is inert (logged) and the
# QA_UPDATE_REQUIRE_SIGNATURE gate decides whether an unsigned release proceeds.
_RELEASE_PUBLIC_KEY_HEX = (
    "4c43769703fb44da543f15402a88c590990f0b0f7c0574caa935c6e16353beff"
)


def _make_writable(path: Path) -> None:
    """Clear the read-only bit ahead of an overwrite/restore. Locked installs
    (QA_CODE_LOCK_ENABLED) keep code files at 0o444; the updater itself must
    still be able to swap them. Never raises."""
    try:
        if path.exists():
            os.chmod(path, os.stat(path).st_mode | stat.S_IWUSR)
    except OSError as exc:
        logger.warning("Could not make %s writable (%s).", path, exc)


def _file_sha256(path: Path) -> Optional[str]:
    """Streaming SHA-256 hexdigest of a file; ``None`` when unreadable (an
    unreadable file counts as modified for integrity purposes)."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def load_manifest(install_dir: Path) -> dict:
    """Parse ``MANIFEST.sha256`` (``<sha256-hex>  <rel/posix/path>`` per line)
    into ``{rel_path: hexdigest}``. A missing/unreadable manifest or malformed
    line degrades to an empty/partial dict — integrity checks become a no-op
    rather than an error. Protected paths are never accepted from a manifest."""
    manifest = install_dir / _MANIFEST_NAME
    entries: dict = {}
    try:
        if not manifest.is_file():
            return entries
        for line in manifest.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 1)
            if len(parts) != 2 or not re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
                continue
            rel = parts[1].strip()
            if rel and ".." not in rel.split("/") and not _is_protected(rel):
                entries[rel] = parts[0].lower()
    except OSError as exc:
        logger.warning("Could not read %s (%s).", _MANIFEST_NAME, exc)
    return entries


def verify_integrity(install_dir: Path) -> list:
    """Return manifest-listed files whose on-disk content is missing or differs
    from the release hash (i.e. locally edited). Empty manifest -> ``[]``."""
    mismatched = []
    for rel, digest in sorted(load_manifest(install_dir).items()):
        if _file_sha256(install_dir / rel) != digest:
            mismatched.append(rel)
    return mismatched


def lock_files(install_dir: Path) -> int:
    """chmod every manifest-listed code file (plus manifest/launcher/VERSION)
    read-only (0o444) so manual or editor saves fail with a permission error.
    Best-effort per file (failures logged, never raised); returns the count of
    files locked."""
    locked = 0
    targets = list(load_manifest(install_dir).keys()) + list(_LOCK_EXTRA)
    for rel in dict.fromkeys(targets):
        path = install_dir / rel
        try:
            if path.is_file():
                # Shell scripts get read+exec unconditionally: zip extraction
                # (updates/heals) drops POSIX modes, so start.sh would arrive
                # 0o644 and a bare exec-bit-preserve would leave it unrunnable.
                # Everything else: read-only, keeping any existing exec bits.
                # ops-5 (issue 5): chmod ONLY when the mode is actually wrong.
                # This ran unconditionally on every update check -- 59 chmods
                # every 15 minutes on an install that was already locked, each
                # one logged at INFO as if it were work. The guard is
                # deliberately "this file is not in the desired mode", NOT "the
                # version looks unchanged": the lock is a security control, so a
                # file that drifted writable (editor, stray chmod, partial
                # extraction) must still be re-locked on the very next pass.
                st_mode = os.stat(path).st_mode
                # The .sh exec-bit rule is POSIX-only. Windows synthesizes the
                # mode from the read-only ATTRIBUTE, so a locked .sh reports
                # 0o444 and could never equal 0o555 -- the guard above would
                # miss every time and re-chmod the shell scripts on every
                # 15-minute pass, logging each as if it were work.
                if rel.endswith(".sh") and os.name != "nt":
                    desired = 0o555
                else:
                    desired = 0o444 | (st_mode & 0o111)
                if stat.S_IMODE(st_mode) != desired:
                    os.chmod(path, desired)
                    locked += 1
        except OSError as exc:
            logger.warning("Could not lock %s (%s).", rel, exc)
    return locked


def verify_manifest_signature(tree: Path) -> str:
    """Verify ``MANIFEST.sig`` (base64 Ed25519 signature over the raw bytes of
    ``MANIFEST.sha256``) against the embedded public key. Returns one of:

    * ``"valid"``   -- signature present and cryptographically verified
    * ``"missing"`` -- no ``MANIFEST.sig``, no embedded key, or cryptography absent
    * ``"invalid"`` -- signature present but does NOT verify (tamper/forgery)

    Never raises: any unexpected error on a PRESENT signature is treated as
    ``"invalid"`` (fail-closed), while a genuinely absent signature/key is
    ``"missing"`` so the caller's policy gate can decide."""
    manifest = tree / _MANIFEST_NAME
    sig_file = tree / _MANIFEST_SIG_NAME
    if not _RELEASE_PUBLIC_KEY_HEX.strip():
        return "missing"
    if not sig_file.is_file() or not manifest.is_file():
        return "missing"
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )
    except ImportError:
        logger.warning(
            "cryptography not installed -- cannot verify MANIFEST.sig (treating "
            "as unsigned)."
        )
        return "missing"
    try:
        pub = Ed25519PublicKey.from_public_bytes(
            bytes.fromhex(_RELEASE_PUBLIC_KEY_HEX.strip())
        )
        signature = base64.b64decode(sig_file.read_text(encoding="utf-8").strip())
        pub.verify(signature, manifest.read_bytes())
        return "valid"
    except InvalidSignature:
        logger.warning("MANIFEST.sig failed Ed25519 verification (%s).", tree)
        return "invalid"
    except Exception as exc:
        logger.warning("MANIFEST.sig verification error (%s) at %s.", exc, tree)
        return "invalid"


def _signature_gate_ok(tree: Path, *, require: bool, context: str) -> bool:
    """Apply the release-signature policy before a manifest tree is trusted for
    install/heal. Returns True to PROCEED, False to ABORT. Never raises.

    * valid   -> proceed
    * invalid -> ALWAYS abort (tamper/forgery), regardless of ``require``
    * missing -> abort iff ``require`` (QA_UPDATE_REQUIRE_SIGNATURE); otherwise
      proceed with a loud migration warning."""
    status = verify_manifest_signature(tree)
    if status == "valid":
        logger.info("Release signature verified (%s).", context)
        return True
    if status == "invalid":
        logger.warning(
            "ABORTING %s: MANIFEST.sig is present but INVALID -- refusing to "
            "trust this release; staying on the current version.",
            context,
        )
        return False
    if require:
        logger.warning(
            "ABORTING %s: no valid MANIFEST.sig and QA_UPDATE_REQUIRE_SIGNATURE "
            "is on -- refusing an unsigned release; staying on the current "
            "version.",
            context,
        )
        return False
    logger.warning(
        "%s: proceeding with an UNSIGNED release (no MANIFEST.sig / no embedded "
        "public key). Set QA_UPDATE_REQUIRE_SIGNATURE=true once all releases are "
        "signed.",
        context,
    )
    return True


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


def _manifest_binding_ok(new_tree: Path) -> bool:
    """Bind a release tree to its MANIFEST.sha256 before it is trusted for a swap.

    Guarantees a VALID signature implies the installed bytes match the signed
    manifest (closes the C1 signing-bypass: a genuine (manifest, sig) pair could
    otherwise ship alongside modified/added code). Two checks:

    * every manifest-listed file matches on disk (``verify_integrity == []``), and
    * every code file the swap would copy is LISTED in the manifest (no unlisted /
      smuggled files, except the manifest + detached signature themselves).

    Policy by signature status:
      * signed ("valid")    -> binding is MANDATORY; a signed tree with no readable
        manifest, a mismatch, or an unlisted file is REJECTED.
      * unsigned ("missing") -> apply the SAME strict binding ONLY when a
        MANIFEST.sha256 is present (integrity is independent of the signature); a
        manifest-less legacy release keeps today's behavior and proceeds.

    Returns True to PROCEED, False to ABORT. Never raises."""
    try:
        signed = verify_manifest_signature(new_tree) == "valid"
        has_manifest = (new_tree / _MANIFEST_NAME).is_file()
        if not has_manifest:
            if signed:
                logger.warning(
                    "ABORTING: a signed release has no readable %s to bind the "
                    "signature to its files -- refusing.",
                    _MANIFEST_NAME,
                )
                return False
            # Legacy unsigned, manifest-less release: preserve current behavior.
            return True
        mismatched = verify_integrity(new_tree)
        if mismatched:
            logger.warning(
                "ABORTING: release tree does not match its %s (%d file(s) differ, "
                "e.g. %s) -- refusing to install unverified bytes.",
                _MANIFEST_NAME,
                len(mismatched),
                mismatched[:5],
            )
            return False
        listed = set(load_manifest(new_tree).keys())
        unlisted = [
            rel
            for rel in _swap_candidate_files(new_tree)
            if rel not in listed and rel not in _MANIFEST_UNLISTED_OK
        ]
        if unlisted:
            logger.warning(
                "ABORTING: release tree carries %d file(s) absent from %s "
                "(e.g. %s) -- refusing to install unlisted code.",
                len(unlisted),
                _MANIFEST_NAME,
                unlisted[:5],
            )
            return False
        return True
    except Exception:
        logger.warning(
            "manifest-binding check failed -- refusing the swap (fail-closed).",
            exc_info=True,
        )
        return False


def _check_embedded_pubkey() -> None:
    """Startup footgun guard: warn if QA_UPDATE_REQUIRE_SIGNATURE is ON but
    _RELEASE_PUBLIC_KEY_HEX is empty -- the setting would reject every release."""
    if settings.qa_update_require_signature and not _RELEASE_PUBLIC_KEY_HEX.strip():
        logger.warning(
            "QA_UPDATE_REQUIRE_SIGNATURE is ON but _RELEASE_PUBLIC_KEY_HEX is empty "
            "in tools/updater.py -- every release will be REJECTED. "
            "Either (a) embed a public key and rebuild, or (b) set "
            "QA_UPDATE_REQUIRE_SIGNATURE=false in .env to allow unsigned releases "
            "during migration."
        )


def _auth_headers(token: str) -> dict:
    """Build request headers. The bearer token is added ONLY when set, and this
    is the single place a token ever touches a header — it is never logged."""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


# GitHub answers an exhausted quota with 403 (primary limit) or 429
# (secondary), and in BOTH cases sets `x-ratelimit-remaining: 0`. Before
# SHYJ-5138 (2026-08-21) that landed in run_update_check's blanket
# `except Exception` as "Startup update check failed (...)" -- the SAME line a
# DNS failure produces -- so the live install whose quota was spent (Cursor
# client log, 21:33:55) read as broken rather than BLIND, and the remedy was
# nowhere in the record. A 401, a 404, or a 403 that is NOT a quota must stay
# a plain error, so the remaining-header check is what separates the two.
_RATE_LIMIT_STATUSES = (403, 429)


def _rate_limit_detail(exc: Exception) -> Optional[str]:
    """Short human note when ``exc`` is a GitHub rate-limit refusal, else None.

    Never raises, and never reads or logs the token. A missing or garbled
    header degrades to a LESS SPECIFIC note; anything unparseable enough to
    throw returns None, so the caller falls back to the generic error path
    rather than mislabelling an unrelated failure as a rate limit."""
    try:
        resp = getattr(exc, "response", None)
        if resp is None:
            return None
        if getattr(resp, "status_code", None) not in _RATE_LIMIT_STATUSES:
            return None
        headers = getattr(resp, "headers", None) or {}
        remaining = str(headers.get("x-ratelimit-remaining", "")).strip()
        if remaining != "0":
            return None
        limit = str(headers.get("x-ratelimit-limit", "")).strip()
        authed = limit.isdigit() and int(limit) > 60
        who = "authenticated" if authed else "unauthenticated, 60/hour"
        wait = ""
        reset = str(headers.get("x-ratelimit-reset", "")).strip()
        if reset.isdigit():
            mins = max(0, int((int(reset) - time.time()) // 60))
            wait = f", resets in ~{mins} min"
        return f"{who}{wait}"
    except Exception:
        logger.debug("rate-limit detail parse failed", exc_info=True)
        return None


def _parse_version(value: str) -> Optional[tuple]:
    """Parse a ``X.Y.Z`` (optionally ``v``-prefixed) version to an int tuple.

    Returns ``None`` for anything without at least one numeric component, so a
    malformed version is treated as "unknown" (never newer) rather than raising.
    """
    v = (value or "").strip().lstrip("vV")
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)", v)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    nums = re.findall(r"\d+", v)
    if not nums:
        return None
    parts = [int(x) for x in nums[:3]]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_newer(remote: str, local: Optional[str]) -> bool:
    """True only when ``remote`` parses to a strictly greater version than
    ``local``. Any unparseable side yields ``False`` (safe: no update)."""
    r = _parse_version(remote or "")
    lo = _parse_version(local or "")
    if r is None or lo is None:
        return False
    return r > lo


def _local_version(install_dir: Path) -> Optional[str]:
    """Read the installed version from ``pyproject.toml`` (``version = "..."``),
    falling back to a ``VERSION`` file. Returns ``None`` if neither is readable —
    the caller then skips the update (can't compare safely). Never raises."""
    try:
        pyproject = install_dir / "pyproject.toml"
        if pyproject.is_file():
            text = pyproject.read_text(encoding="utf-8")
            m = re.search(r'(?m)^\s*version\s*=\s*"([^"]+)"', text)
            if m:
                return m.group(1).strip()
        version_file = install_dir / "VERSION"
        if version_file.is_file():
            return version_file.read_text(encoding="utf-8").strip() or None
    except OSError as exc:
        logger.warning("Could not read local version (%s).", exc)
    return None


# One GET per process per TTL: startup, qa-doctor and the periodic drift
# watch each hit /releases/latest, so a single session made three identical
# calls within 11 seconds (observed 2026-08-03 22:11:14-22:11:25). 300s keeps
# "publish a release, run qa-doctor" responsive while collapsing the
# burst -- and keeps the network off the qa-doctor hot path within the
# TTL. Only SUCCESS is cached; errors keep raising so callers degrade as
# before and the next call retries for real.
_RELEASE_CACHE_TTL_S = 300.0
_release_cache: dict[str, tuple[float, dict]] = {}
_release_cache_lock = threading.Lock()


def fetch_latest_release(repo: str, token: str, timeout: float) -> Optional[dict]:
    """TTL-cached wrapper over the GitHub release lookup (cache note above).
    Same contract as the uncached fetch: returns the release dict or raises on
    a network/HTTP error (the caller catches and degrades)."""
    now = time.monotonic()
    with _release_cache_lock:
        hit = _release_cache.get(repo)
        if hit and (now - hit[0]) < _RELEASE_CACHE_TTL_S:
            return dict(hit[1])
    data = _fetch_latest_release_uncached(repo, token, timeout)
    if data:
        with _release_cache_lock:
            _release_cache[repo] = (now, dict(data))
    return data


def _fetch_latest_release_uncached(
    repo: str, token: str, timeout: float
) -> Optional[dict]:
    """GET /repos/{repo}/releases/latest. Returns ``{tag_name, zipball_url}`` or
    raises on a network/HTTP error (the caller catches and degrades)."""
    url = f"{_GITHUB_API}/repos/{repo}/releases/latest"
    resp = httpx.get(
        url, headers=_auth_headers(token), timeout=timeout, follow_redirects=True
    )
    resp.raise_for_status()
    data = resp.json()
    asset_url = ""
    for asset in data.get("assets") or []:
        name = str(asset.get("name") or "")
        if name.endswith(".zip") and asset.get("browser_download_url"):
            asset_url = asset["browser_download_url"]
            break
    return {
        "tag_name": data.get("tag_name"),
        "zipball_url": data.get("zipball_url"),
        "asset_url": asset_url,
    }


def _restore_exec_bits(zf: zipfile.ZipFile, dest: Path) -> None:
    """Re-apply the archive's EXECUTE bits after extraction.

    ``ZipFile.extractall`` does not restore the POSIX mode, even though
    ``_safe_extract_zip`` has already parsed ``external_attr`` for its symlink
    check and could simply keep it. Everything executable in a release therefore
    landed non-executable. ``apply_update`` had a suffix-scoped workaround for
    ``*.sh`` only, so ``connect.sh`` survived and nothing else did; the sibling
    path shipped a tester-facing "./mvnw is not executable" message for the same
    root cause (framework_writer.py), which is this class reaching a real user.

    Bounded on purpose: the execute bits the archive itself carried, OR-ed onto
    what is there. Never setuid/setgid/sticky, never a bit the archive did not
    carry, and an OSError on one member is skipped rather than failing an
    extraction that has already passed every containment check.
    """
    for info in zf.infolist():
        if info.is_dir():
            continue
        exec_bits = (info.external_attr >> 16) & 0o111
        if not exec_bits:
            continue
        target = dest / info.filename
        if target.is_symlink() or not target.is_file():
            continue
        try:
            target.chmod(stat.S_IMODE(target.stat().st_mode) | exec_bits)
        except OSError:
            continue


def _safe_extract_zip(zf: zipfile.ZipFile, dest: Path) -> None:
    """Extract a release zip, refusing any member that would escape ``dest``.

    Zip-slip defense-in-depth: absolute paths, any ``..`` component, a
    target that resolves outside ``dest``, or a symlink member each abort
    the whole extraction with ``ValueError`` (the caller degrades to the
    'error' status and starts the current version). Never extracts a
    single member before the whole archive has been validated."""
    dest_root = dest.resolve()
    for info in zf.infolist():
        name = info.filename
        if name.startswith(("/", "\\")) or ".." in Path(name).parts:
            raise ValueError(f"Unsafe zip member path: {name!r}")
        target = (dest / name).resolve()
        if target != dest_root and dest_root not in target.parents:
            raise ValueError(f"Zip member escapes extract dir: {name!r}")
        # POSIX mode lives in the high 16 bits of external_attr for
        # zips created on Unix (create_system==3); GitHub source
        # zipballs qualify. On non-POSIX zips these bits are 0, so the
        # path/traversal checks above remain the primary guard.
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise ValueError(f"Unsafe symlink in zip: {name!r}")
    zf.extractall(dest)
    _restore_exec_bits(zf, dest)


def download_and_extract(
    zipball_url: str, token: str, timeout: float, workdir: Path
) -> Path:
    """Download the release zipball and extract it under ``workdir``. Returns the
    single top-level directory GitHub wraps the source in (``owner-repo-sha/``)."""
    resp = httpx.get(
        zipball_url,
        headers=_auth_headers(token),
        timeout=timeout,
        follow_redirects=True,
    )
    resp.raise_for_status()
    zip_path = workdir / "release.zip"
    zip_path.write_bytes(resp.content)
    extract_dir = workdir / "extracted"
    with zipfile.ZipFile(zip_path) as zf:
        _safe_extract_zip(zf, extract_dir)
    subdirs = [p for p in extract_dir.iterdir() if p.is_dir()]
    return subdirs[0] if len(subdirs) == 1 else extract_dir


# Files apply_update must NEVER prune, even when a release drops them from its
# tree: release metadata plus the launcher the client spawns (`python
# launcher.py` must keep working across a bad release). Operator state -- .env,
# data/, corpus/, .claude/ -- is already covered by _is_protected.
_NEVER_PRUNE = frozenset(_LOCK_EXTRA) | {".env.example"}

#: Name of the release-shipped list of paths a PAST release installed and a
#: LATER release deleted. Hashed into ``MANIFEST.sha256`` and covered by
#: ``MANIFEST.sig`` like any other shipped file, so it cannot be edited on a
#: client without failing the integrity gates.
_REMOVED_PATHS_NAME = "REMOVED_PATHS"


def load_removed_paths(tree: Path) -> set:
    """Parse ``<tree>/REMOVED_PATHS`` -- one install-relative POSIX path per
    line, blanks and ``#`` comments ignored.

    WHY IT EXISTS: ``_prunable``'s condition (c) -- "the install's OLD manifest
    lists it" -- is what makes the forward-looking prune safe, and it is also
    what makes it unable to reach a file STRANDED by a release predating the
    prune. Such a file was installed by an older release whose manifest listed
    it, and that manifest has since been overwritten, so no evidence of it
    survives on the client. This file is that evidence, shipped forward.

    It REPLACES condition (c) with a different safety property -- an explicit,
    human-authored, code-reviewed list inside a SIGNED release, rather than an
    inference from client state. The other guards are UNCHANGED and still
    apply to every entry: a listed path is deleted only when it is absent from
    the new tree, is not protected/derived/never-prune, and the release tree
    carried its own manifest.

    Lines that could escape the install root are DROPPED, not honoured:
    absolute paths, drive letters, backslashes, and any ``..`` component. A
    malformed line is a no-op, never a traversal. Never raises -- a missing or
    unreadable file yields an empty set, which simply means "prune nothing
    extra"."""
    out: set = set()
    try:
        raw = (tree / _REMOVED_PATHS_NAME).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return out
    for line in raw.splitlines():
        rel = line.split("#", 1)[0].strip()
        if not rel:
            continue
        if "\\" in rel or rel.startswith("/") or ":" in rel:
            logger.warning("REMOVED_PATHS: ignoring non-relative entry %r.", rel)
            continue
        parts = [p for p in rel.split("/") if p and p != "."]
        if not parts or ".." in parts:
            logger.warning("REMOVED_PATHS: ignoring traversing entry %r.", rel)
            continue
        out.add("/".join(parts))
    return out


def _prunable(
    old_manifest: dict,
    new_rels: set,
    install_dir: Path,
    removed: Optional[set] = None,
) -> list:
    """Install files a PREVIOUS release installed that the NEW release dropped.

    Three conditions, all required, and (c) is the safety property:
    (a) the file is on disk in the install, (b) it is absent from the new
    release tree, and (c) the install's OLD ``MANIFEST.sha256`` lists it. (c) is
    what restricts deletion to files this updater itself installed, so a
    tester's own scratch file -- or anything under ``data/`` / ``corpus/`` --
    can never be a candidate whatever else is true of it.

    WHY: ``apply_update`` was a pure OVERLAY, so a module a release DELETED
    stayed on every installed machine forever. The MOTIVATING SYMPTOM, measured
    on the live install after it self-updated 1.60.3 -> 1.60.4: eight unlisted
    ``.py`` files in ``tools/`` -- ``comment_reconciler``, ``image_description``,
    ``jira_attachments``, ``requirement_analyzer``, ``test_plan_report``,
    ``token_meter``, ``web_search``, ``zephyr_exporter``. Several were deleted
    specifically so the capability could not be revived
    (docs/RETIRED_CAPABILITIES.md), which leaving them installed defeats -- and
    ``verify_integrity`` cannot see them either, because it only walks manifest
    ENTRIES.

    Condition (c) alone is FORWARD-LOOKING and could never reach those eight:
    they are absent from the CURRENT manifest, having been installed by an
    older release whose manifest was overwritten. ``removed`` is the second
    admission route that does reach them -- the release-shipped
    ``REMOVED_PATHS`` list (see ``load_removed_paths``), which substitutes an
    explicit signed declaration for the client-state inference of (c). Every
    OTHER guard applies identically to both routes: absent from the new tree,
    not protected/derived/never-prune, backed up before unlink. See
    operations/runbook.md -> *Files a release removes*.

    Honours ``_is_protected`` and ``_is_derived_artifact`` exactly as the copy
    loop does, so the two skip sets stay identical. Pure; never raises."""
    out = []
    for rel in sorted(set(old_manifest) | set(removed or ())):
        if rel in new_rels or rel in _NEVER_PRUNE:
            continue
        if _is_protected(rel) or _is_derived_artifact(rel):
            continue
        target = install_dir / rel
        if target.is_file() and not target.is_symlink():
            out.append(rel)
    return out


def _rollback(
    install_dir: Path,
    backup_dir: Path,
    overwritten: list,
    created: list,
    pruned: Optional[list] = None,
) -> None:
    """Undo a partial swap: delete newly-created files, restore overwritten ones
    from the backup, and put back anything the prune pass removed. Best-effort —
    individual failures are logged, not raised."""
    for rel in created:
        try:
            (install_dir / rel).unlink(missing_ok=True)
        except OSError as exc:  # pragma: no cover - defensive
            logger.warning("Rollback: could not remove new file %s (%s).", rel, exc)
    for rel in overwritten:
        backup = backup_dir / rel
        if backup.exists():
            try:
                _make_writable(install_dir / rel)
                shutil.copy2(backup, install_dir / rel)
            except OSError as exc:  # pragma: no cover - defensive
                logger.warning("Rollback: could not restore %s (%s).", rel, exc)
    for rel in pruned or ():
        backup = backup_dir / rel
        if backup.exists():
            try:
                (install_dir / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup, install_dir / rel)
            except OSError as exc:  # pragma: no cover - defensive
                logger.warning("Rollback: could not restore pruned %s (%s).", rel, exc)


_UPDATE_LOCK_FILE = ".update.lock"

#: How long a holder may keep the lock before a loser reports it as wedged.
#: An ESTIMATE, not a measurement -- no swap on this install has ever been
#: timed. It is deliberately far above any plausible swap so the CRITICAL line
#: below means "something is wrong, look at this" and never "this is busy".
HELD_TOO_LONG_S = 600


class LockUnsupported(Exception):
    """Neither OS locking primitive is available on this interpreter."""


def _os_lock(fd: int) -> bool:
    """Take an exclusive, NON-BLOCKING OS lock on *fd*. ``True`` if we got it.

    THE KERNEL OWNS LIVENESS. An advisory lock is released when the holding
    process exits -- cleanly, killed, or crashed -- so there is no such thing
    as a stale lock: nothing to detect, nothing to steal, and no check-then-act
    window in which two processes both decide to steal. Three review rounds of
    pid-and-timestamp bookkeeping were deleted rather than corrected, because
    each of their defects came from asking the lock FILE a question only the
    kernel can answer, and each fix for one direction opened the other.

    Non-blocking on purpose: this runs on the launcher's update poll, and a
    loser must skip the pass rather than hold the poll thread.
    """
    try:
        import fcntl
    except ImportError:
        pass
    else:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False
    try:
        import msvcrt
    except ImportError as exc:  # pragma: no cover - neither primitive exists
        raise LockUnsupported("neither fcntl nor msvcrt is available") from exc
    # WINDOWS, UNVERIFIED: no Windows machine has run this code, the same
    # standing disclosure the mobile lane carries. `msvcrt.locking` locks a
    # byte REGION from the current file position and that region has to
    # EXIST, so this is not the POSIX call under another name -- the caller
    # guarantees the file is at least one byte before we get here.
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        return True
    except OSError:
        return False


def _os_unlock(fd: int) -> None:
    """Release what :func:`_os_lock` took. Never raises: the close below, and
    ultimately process exit, releases it regardless."""
    try:
        import fcntl
    except ImportError:
        pass
    else:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            logger.debug("tools.updater: could not release the update lock")
        return
    try:
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    except (ImportError, OSError):  # pragma: no cover - Windows only
        logger.debug("tools.updater: could not release the update lock")


def _lock_holder_note(lock_path: Path) -> str:
    """A human-readable description of who holds the lock, for LOGS ONLY.

    Nothing branches on this. That is the whole point: the previous design read
    this body to decide whether the holder was alive and whether the lock could
    be taken, and got a defect in each direction. It is diagnostics now, and a
    wrong or missing answer costs a less specific log line and nothing else.
    """
    try:
        body = lock_path.read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown holder"
    head, _, stamp = body.partition(" ")
    try:
        held = max(0.0, time.time() - float(stamp))
    except ValueError:
        return "holder " + (head[:40] or "unknown")
    return (
        "holder " + (head[:40] or "unknown") + ", holding for " + str(int(held)) + "s"
    )


def _held_too_long(lock_path: Path) -> float:
    """Seconds the current holder has held the lock, or ``0.0`` if unknown."""
    try:
        body = lock_path.read_text(encoding="utf-8").strip()
        return max(0.0, time.time() - float(body.partition(" ")[2]))
    except (OSError, ValueError):
        return 0.0


@contextlib.contextmanager
def _update_lock(install_dir: Path):
    """Serialise ``apply_update`` ACROSS PROCESSES with an OS lock.

    Three launcher pids on the same install_dir can start an update at once --
    observed 2026-09-03: two of three aborted with "release tree does not match
    its MANIFEST.sha256", because the swap is not atomic against a concurrent
    overlay.

    Yields ``True`` when the lock was taken (released on exit from this
    context), ``False`` otherwise -- and never raises for any of it. A caller
    that cannot even reach the lock is contention's twin: a read-only or absent
    install_dir, or a directory sitting on the lock path, must cost one update
    poll rather than the launcher.

    The lock FILE is never unlinked and never read to make a decision. It
    exists so the kernel has something to lock and so a human has something to
    read; its contents are diagnostics.
    """
    lock_path = install_dir / _UPDATE_LOCK_FILE
    if lock_path.is_dir():
        logger.warning(
            "tools.updater: %s is a DIRECTORY, so no update can take the lock. "
            "Remove it to let updates resume.",
            lock_path,
        )
        yield False
        return
    fd = None
    locked = False
    try:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            # The region msvcrt will lock has to exist, and an empty file has
            # no byte 0. Written BEFORE the lock and overwritten after it, so
            # the identity in the file is always the winner's.
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
        except OSError as exc:
            logger.warning(
                "tools.updater: could not open an update lock in %s (%s); "
                "skipping this pass.",
                install_dir,
                exc,
            )
            yield False
            return
        try:
            locked = _os_lock(fd)
        except LockUnsupported as exc:  # pragma: no cover - unreachable on CPython
            # FAIL CLOSED. A skipped update costs one poll interval; a
            # concurrent overlay corrupts the install tree.
            logger.warning(
                "tools.updater: no OS file locking on this platform (%s), so an "
                "update cannot be serialised; skipping this pass.",
                exc,
            )
            yield False
            return
        if not locked:
            held = _held_too_long(lock_path)
            if held > HELD_TOO_LONG_S:
                # NOT a force-release: breaking a lock whose holder may be
                # mid-swap is the concurrent overlay this exists to prevent,
                # and "is the holder wedged" is the same liveness question the
                # file has already proved it cannot answer. So this reports,
                # and a human decides.
                logger.critical(
                    "tools.updater: the update lock in %s has been held for "
                    "%.0fs (%s). This install has stopped updating; check "
                    "whether that process is wedged.",
                    install_dir,
                    held,
                    _lock_holder_note(lock_path),
                )
            else:
                logger.info(
                    "tools.updater: another process is applying an update to "
                    "%s (%s); skipping this pass.",
                    install_dir,
                    _lock_holder_note(lock_path),
                )
            yield False
            return
        # Ours. Record who we are, for the log line above in some OTHER
        # process -- never for a decision here.
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            os.ftruncate(fd, 0)
            os.write(
                fd,
                (
                    str(os.getpid())
                    + ":"
                    + secrets.token_hex(8)
                    + " "
                    + str(time.time())
                ).encode("utf-8"),
            )
        except OSError:
            logger.debug("tools.updater: could not stamp the update lock")
        yield True
    finally:
        if fd is not None:
            if locked:
                _os_unlock(fd)
            try:
                os.close(fd)
            except OSError:
                pass


def apply_update_locked(
    new_tree: Path, install_dir: Path, version: str = "update"
) -> Optional[Path]:
    """``apply_update``, serialised across OS processes (2026-09-03, D6).

    Only ONE racing process actually applies; the others log a clear line and
    return ``None`` WITHOUT raising, so a race never surfaces as a
    "MANIFEST.sha256 does not match" abort -- it surfaces as nothing, because
    the winner already did the work. A real apply failure still propagates
    from :func:`apply_update` unchanged.
    """
    with _update_lock(install_dir) as acquired:
        if not acquired:
            logger.info(
                "tools.updater: another process is already applying an update "
                "to %s; skipping this pass.",
                install_dir,
            )
            return None
        return apply_update(new_tree, install_dir, version=version)


def apply_update(new_tree: Path, install_dir: Path, version: str = "update") -> Path:
    """Swap the new release tree into the install, skipping every protected
    path. PRUNES files a previous release installed that this one dropped (see
    ``_prunable``), then overlays the new tree. Backs each pruned/replaced/
    created file up under ``backups/pre-update-*`` and rolls the whole swap back
    on any error (then re-raises). Returns the backup directory on success."""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = install_dir / "backups" / f"pre-update-{version}-{ts}"
    overwritten: list = []
    created: list = []
    pruned: list = []
    # Read BEFORE anything is written: the swap below overwrites
    # MANIFEST.sha256 with the NEW release's copy, and the old listing is the
    # only record of what this updater put on disk.
    old_manifest = load_manifest(install_dir)
    try:
        plan = []
        for src in sorted(new_tree.rglob("*")):
            if not src.is_file():
                continue
            rel = src.relative_to(new_tree).as_posix()
            if _is_protected(rel) or _is_derived_artifact(rel):
                continue
            plan.append((src, rel))
        # PRUNE FIRST, so a failure anywhere in the copy below rolls the
        # deletions back along with everything else -- a half-swapped install
        # must not also be missing modules. Each deletion is individually
        # guarded: one un-deletable orphan is logged and skipped, never allowed
        # to fail an otherwise good update.
        #
        # ...but ONLY against a release tree that carries its own manifest.
        # `new_rels` is the evidence that decides what gets DELETED, so an
        # incomplete or mis-rooted `new_tree` does not merely under-copy, it
        # over-deletes: every path in the old manifest that the tree happens
        # not to contain satisfies all three of _prunable's conditions.
        # _manifest_binding_ok has exactly ONE permissive branch -- a tree with
        # no MANIFEST.sha256, waved through to "preserve current behavior" --
        # and that permission was written when the current behaviour was a pure
        # OVERLAY, which is non-destructive by construction. Prune changes what
        # it grants, so it does not inherit it. Note the two hazards coincide:
        # download_and_extract picks its root by counting DIRECTORIES only, so
        # a mis-rooted tree also has no manifest at its root and is caught here.
        new_rels = {rel for _src, rel in plan}
        if not (new_tree / _MANIFEST_NAME).is_file():
            logger.warning(
                "Release tree has no %s -- skipping the prune pass (the copy "
                "still runs). Nothing is deleted on an unvalidated tree.",
                _MANIFEST_NAME,
            )
            prunable: list = []
        else:
            prunable = _prunable(
                old_manifest,
                new_rels,
                install_dir,
                load_removed_paths(new_tree),
            )
        for rel in prunable:
            dest = install_dir / rel
            try:
                bdest = backup_dir / rel
                bdest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dest, bdest)
                _make_writable(dest)
                dest.unlink()
            except OSError as exc:
                logger.warning("Could not prune %s (%s).", rel, exc)
                continue
            pruned.append(rel)
        for src, rel in plan:
            dest = install_dir / rel
            if dest.exists():
                bdest = backup_dir / rel
                bdest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dest, bdest)
                overwritten.append(rel)
            else:
                created.append(rel)
            dest.parent.mkdir(parents=True, exist_ok=True)
            _make_writable(dest)
            shutil.copy2(src, dest)
            if rel.endswith(".sh"):
                # RETAINED, and not redundant now that _safe_extract_zip
                # restores modes. That restore can only replay bits the archive
                # CARRIED: a zip built on Windows has create_system != 3 and no
                # POSIX bits at all, so `external_attr >> 16` is 0 and there is
                # nothing to put back. This suffix rule is the only thing
                # covering that case, and without it a shell script would land
                # non-executable for the window between this copy and the later
                # lock_files() pass (which only runs after _pip_install() /
                # migrate_env() finish) -- long enough for a client's spawn
                # attempt to hit EACCES.
                st_mode = os.stat(dest).st_mode
                os.chmod(dest, st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        logger.info(
            "Applied update: %d file(s) (%d new, %d replaced, %d pruned). Backup at %s",
            len(created) + len(overwritten),
            len(created),
            len(overwritten),
            len(pruned),
            backup_dir,
        )
        return backup_dir
    except Exception:
        logger.warning("Update swap failed mid-way — rolling back from backup.")
        _rollback(install_dir, backup_dir, overwritten, created, pruned)
        raise


def _env_line_key(line: str) -> str:
    """KEY of an active ``KEY=...`` line ('' for comments/blank/malformed)."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return ""
    return stripped.split("=", 1)[0].strip()


def _commented_env_key(line: str) -> str:
    """KEY of a commented-out ``# KEY=...`` line ('' when not that shape)."""
    stripped = line.strip()
    if not stripped.startswith("#"):
        return ""
    body = stripped.lstrip("#").strip()
    if "=" not in body:
        return ""
    key = body.split("=", 1)[0].strip()
    return key if key.isidentifier() or key.replace("_", "").isalnum() else ""


def migrate_env(install_dir: Path) -> int:
    """Append config keys newly shipped in ``.env.example`` to the user's
    ``.env`` after an update. User lines are NEVER modified, reordered or
    removed; keys the user commented out count as present (deliberate
    opt-out); template-commented keys are docs and are not propagated.
    Returns the number of keys added; never raises."""
    try:
        example = install_dir / ".env.example"
        env = install_dir / ".env"
        if not example.is_file() or not env.is_file():
            return 0
        user_lines = env.read_text(encoding="utf-8").splitlines()
        user_keys = set()
        for line in user_lines:
            key = _env_line_key(line) or _commented_env_key(line)
            if key:
                user_keys.add(key)
        additions = []
        for line in example.read_text(encoding="utf-8").splitlines():
            key = _env_line_key(line)
            if key and key not in user_keys:
                additions.append(line.strip())
                user_keys.add(key)
        if not additions:
            return 0
        version = _local_version(install_dir) or "?"
        stamp = datetime.now().strftime("%Y-%m-%d")
        block = [
            "",
            f"# --- new settings added by the v{version} update ({stamp}) ---",
        ] + additions
        env.write_text("\n".join(user_lines + block) + "\n", encoding="utf-8")
        logger.info(
            "migrate_env: appended %d new setting(s) to .env: %s",
            len(additions),
            ", ".join(a.split("=", 1)[0] for a in additions),
        )
        return len(additions)
    except Exception:
        logger.exception("migrate_env failed — user .env left unchanged")
        return 0


def _pip_install(install_dir: Path) -> None:
    """Run ``pip install -e .`` after a swap (deps may have changed). A failure
    is logged, not raised — startup proceeds on the newly-swapped code."""
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", "."],
            cwd=str(install_dir),
            check=True,
            timeout=_PIP_TIMEOUT,
            capture_output=True,
        )
    except Exception as exc:
        logger.warning(
            "pip install -e . after update failed (%s) — dependencies may be stale.",
            exc,
        )


def run_update_check(
    install_dir: Optional[Path] = None,
    *,
    force: bool = False,
    repo_override: Optional[str] = None,
    lock_override: Optional[bool] = None,
) -> str:
    """Startup self-update + integrity pass. Returns a status string and NEVER
    raises: ``"disabled" | "no-repo" | "up-to-date" | "updated" | "healed" |
    "heal-aborted" | "rate-limited" | "error"``. On any failure the caller starts the current version.

    ``force`` / ``repo_override`` / ``lock_override`` let a distribution
    launcher mandate the check regardless of local ``.env`` toggles; developer
    checkouts keep the opt-in settings defaults (all OFF).
    """
    if install_dir is None:
        install_dir = _INSTALL_DIR
    try:
        _check_embedded_pubkey()
        # QA_AUTO_UPDATE_ENABLED was DELETED on 2026-08-13 (flag-surface
        # reduction, batch 6) and hardcoded OFF, so a check happens only when a
        # caller FORCES it. The distribution launcher does exactly that
        # (force=True), and its precedence is unchanged -- what is gone is a
        # developer checkout's ability to arm the check from .env.
        if not force:
            logger.info("Auto-update is off unless the caller forces it.")
            return "disabled"
        repo = (repo_override or settings.qa_update_repo or "").strip()
        if not repo:
            logger.warning(
                "An update check was forced but no repo is configured (QA_UPDATE_REPO empty) — skipping update."
            )
            return "no-repo"
        token = (settings.github_token or "").strip()
        timeout = settings.qa_update_timeout
        # QA_CODE_LOCK_ENABLED was DELETED on 2026-08-13 and hardcoded OFF for a
        # developer checkout; lock_override still wins, which is how the
        # distribution launcher (lock_override=True) keeps the lock working.
        lock = False if lock_override is None else lock_override
        local = _local_version(install_dir)
        release = fetch_latest_release(repo, token, timeout)
        if not release or not release.get("tag_name"):
            logger.warning("No release found for %s — starting current version.", repo)
            return "error"
        remote = release["tag_name"]
        zipball = release.get("zipball_url")
        # Prefer the uploaded release ASSET over the auto-generated source
        # zipball so GitHub's per-asset download_count tracks version adoption;
        # fall back to the zipball when no asset was attached.
        download_url = release.get("asset_url") or zipball
        if is_newer(remote, local):
            if not download_url:
                logger.warning(
                    "Release %s has no downloadable archive - skipping update.",
                    remote,
                )
                return "error"
            logger.info(
                "Newer release %s available (local=%s) — updating.", remote, local
            )
            with tempfile.TemporaryDirectory(prefix="qa-update-") as tmp:
                new_tree = download_and_extract(download_url, token, timeout, Path(tmp))
                if not _signature_gate_ok(
                    new_tree,
                    require=settings.qa_update_require_signature,
                    context="update",
                ):
                    return "error"
                # C1: a passed gate only proves the manifest's signature; bind
                # that manifest to the actual tree bytes before trusting the swap.
                if not _manifest_binding_ok(new_tree):
                    return "error"
                if (
                    apply_update_locked(
                        new_tree, install_dir, version=str(remote).lstrip("vV")
                    )
                    is None
                ):
                    return "update-skipped"
            _pip_install(install_dir)
            migrate_env(install_dir)
            if lock:
                lock_files(install_dir)
            logger.info("Update to %s complete.", remote)
            return "updated"
        status = "up-to-date"
        if lock:
            mismatched = verify_integrity(install_dir)
            # Heal only when the latest release matches the installed version —
            # that zipball is the exact tree the local MANIFEST.sha256 describes.
            same_release = (
                bool(download_url)
                and _parse_version(remote or "") is not None
                and _parse_version(remote or "") == _parse_version(local or "")
            )
            if mismatched and same_release:
                logger.warning(
                    "Integrity check: %d locally modified file(s) %s — healing from release %s.",
                    len(mismatched),
                    mismatched[:5],
                    remote,
                )
                with tempfile.TemporaryDirectory(prefix="qa-heal-") as tmp:
                    new_tree = download_and_extract(
                        download_url, token, timeout, Path(tmp)
                    )
                    if _signature_gate_ok(
                        new_tree,
                        require=settings.qa_update_require_signature,
                        context="self-heal",
                    ) and _manifest_binding_ok(new_tree):
                        healed_backup = apply_update_locked(
                            new_tree,
                            install_dir,
                            version="heal-" + str(remote).lstrip("vV"),
                        )
                        status = (
                            "healed" if healed_backup is not None else "heal-skipped"
                        )
                    else:
                        logger.warning(
                            "Self-heal aborted: release signature/manifest not "
                            "trusted; leaving current files in place."
                        )
                        status = "heal-aborted"
            elif mismatched:
                logger.warning(
                    "Integrity check: %d modified file(s) but no release zipball matching "
                    "local version %s — cannot heal.",
                    len(mismatched),
                    local,
                )
            locked = lock_files(install_dir)
            # ops-5 (issue 5): only announce real work. A no-op pass logs at
            # DEBUG, so "Code lock: N file(s)" now means N files ACTUALLY drifted
            # and were re-locked -- worth noticing -- instead of appearing every
            # 15 minutes and training the reader to ignore it.
            if locked:
                logger.info("Code lock: %d file(s) re-locked read-only.", locked)
            else:
                logger.debug("Code lock: all files already read-only.")
        logger.info("Up to date (local=%s, latest=%s).", local, remote)
        return status
    except httpx.HTTPStatusError as exc:
        # Ahead of the blanket clause below ON PURPOSE: an exhausted GitHub
        # quota is not a failure to reach GitHub. Both directions matter --
        # a non-quota 403/401/404 falls through to the generic message and
        # "error", which is what keeps this branch from becoming "every HTTP
        # error is a rate limit".
        detail = _rate_limit_detail(exc)
        if detail is None:
            logger.warning(
                "Startup update check failed (%s) — starting current version.",
                exc,
            )
            return "error"
        logger.warning(
            "Startup update check hit the GitHub API rate limit (%s) — "
            "auto-update is BLIND until it resets, not broken, and this "
            "server is starting the version already on disk. Set GITHUB_TOKEN "
            "in .env to raise the limit from 60 to 5000 requests/hour.",
            detail,
        )
        return "rate-limited"
    except Exception as exc:
        logger.warning(
            "Startup update check failed (%s) — starting current version.", exc
        )
        return "error"
