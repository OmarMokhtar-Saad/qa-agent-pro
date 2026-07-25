"""GitHub-Release startup self-update (``QA_AUTO_UPDATE_ENABLED`` — opt-in, OFF by default).

House rule: this module imports **nothing internal except ``config.settings``**
(the launcher path must not pull in ``app.py`` or any agent). It is invoked by
``launcher.py`` *before* Chainlit/app imports load, so an update can swap code in
place first.

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

import hashlib
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
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
    "operations/auth",  # bcrypt users.json
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


_MANIFEST_NAME = "MANIFEST.sha256"
# Locked read-only in addition to every manifest entry (release metadata + the
# launcher itself, so "python launcher.py" cannot be edited out from under us).
_LOCK_EXTRA = ("MANIFEST.sha256", "launcher.py", "VERSION")


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
                if rel.endswith(".sh"):
                    os.chmod(path, 0o555)
                else:
                    os.chmod(path, 0o444 | (os.stat(path).st_mode & 0o111))
                locked += 1
        except OSError as exc:
            logger.warning("Could not lock %s (%s).", rel, exc)
    return locked


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


def fetch_latest_release(repo: str, token: str, timeout: float) -> Optional[dict]:
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


def _rollback(
    install_dir: Path, backup_dir: Path, overwritten: list, created: list
) -> None:
    """Undo a partial swap: delete newly-created files, restore overwritten ones
    from the backup. Best-effort — individual failures are logged, not raised."""
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


def apply_update(new_tree: Path, install_dir: Path, version: str = "update") -> Path:
    """Overlay the new release tree onto the install, skipping every protected
    path. Backs each replaced/created file up under ``backups/pre-update-*`` and
    rolls the whole swap back on any error (then re-raises). Returns the backup
    directory on success."""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = install_dir / "backups" / f"pre-update-{version}-{ts}"
    overwritten: list = []
    created: list = []
    try:
        for src in sorted(new_tree.rglob("*")):
            if not src.is_file():
                continue
            rel = src.relative_to(new_tree).as_posix()
            if _is_protected(rel):
                continue
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
        logger.info(
            "Applied update: %d file(s) (%d new, %d replaced). Backup at %s",
            len(created) + len(overwritten),
            len(created),
            len(overwritten),
            backup_dir,
        )
        return backup_dir
    except Exception:
        logger.warning("Update swap failed mid-way — rolling back from backup.")
        _rollback(install_dir, backup_dir, overwritten, created)
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
    "error"``. On any failure the caller starts the current version.

    ``force`` / ``repo_override`` / ``lock_override`` let a distribution
    launcher mandate the check regardless of local ``.env`` toggles; developer
    checkouts keep the opt-in settings defaults (all OFF).
    """
    if install_dir is None:
        install_dir = _INSTALL_DIR
    try:
        if not (settings.qa_auto_update_enabled or force):
            logger.info("Auto-update disabled (QA_AUTO_UPDATE_ENABLED=false).")
            return "disabled"
        repo = (repo_override or settings.qa_update_repo or "").strip()
        if not repo:
            logger.warning(
                "QA_AUTO_UPDATE_ENABLED is on but QA_UPDATE_REPO is empty — skipping update."
            )
            return "no-repo"
        token = (settings.github_token or "").strip()
        timeout = settings.qa_update_timeout
        lock = settings.qa_code_lock_enabled if lock_override is None else lock_override
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
                apply_update(new_tree, install_dir, version=str(remote).lstrip("vV"))
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
                    apply_update(
                        new_tree,
                        install_dir,
                        version="heal-" + str(remote).lstrip("vV"),
                    )
                status = "healed"
            elif mismatched:
                logger.warning(
                    "Integrity check: %d modified file(s) but no release zipball matching "
                    "local version %s — cannot heal.",
                    len(mismatched),
                    local,
                )
            locked = lock_files(install_dir)
            logger.info("Code lock: %d file(s) set read-only.", locked)
        logger.info("Up to date (local=%s, latest=%s).", local, remote)
        return status
    except Exception as exc:
        logger.warning(
            "Startup update check failed (%s) — starting current version.", exc
        )
        return "error"
