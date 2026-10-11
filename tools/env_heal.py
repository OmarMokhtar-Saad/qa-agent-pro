"""Repair known-stale values in an install's .env (see qa-doctor).

``updater.migrate_env`` appends missing keys but never rewrites an existing line,
which is the right default for operator config and the reason a stale value can
outlive several releases. This module handles the narrow remainder: a key whose
value is still EXACTLY one the project itself shipped as a default, and which a
later release superseded.

Never raises. Nothing here touches a value the operator chose, or a key they
commented out.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# key -> (superseded values that MAY be rewritten, new value, one-line reason)
#
# Every entry must name values THIS PROJECT once shipped. Anything else is an
# operator decision and is out of scope by construction.
HEAL_RULES: dict[str, tuple[tuple[str, ...], str, str]] = {
    "JIRA_MAX_PARENT_CHARS": (
        ("1500",),
        "2500",
        "the sibling-story budget is additive, so 1500 squeezed out the parent's "
        "own description",
    ),
    "JIRA_FETCH_COMMENTS": (
        ("false", "0", "no", "off"),
        "true",
        "comments now ride along in the same Jira call -- zero extra round trips",
    ),
    "JIRA_FETCH_IMAGES": (
        ("false", "0", "no", "off"),
        "true",
        "names the screenshots a ticket references instead of silently ignoring them",
    ),
    # No JIRA_FETCH_SIBLING_STORIES / JIRA_FETCH_PARENT rule: both are opt-in
    # (default false), so "false" is the current default, not a superseded one.
}

_MAX_ENV_BYTES = 1_000_000


def _split(line: str) -> tuple[str, str]:
    """(KEY, value) for an ACTIVE assignment line, ('', '') otherwise."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return "", ""
    key, _, value = stripped.partition("=")
    return key.strip(), value.strip()


def _scan(raw: str) -> tuple[list[str], list[tuple[str, str, str, str]]]:
    """(rewritten lines, [(key, old, new, reason), ...]) for ``raw``. Pure."""
    lines = raw.splitlines()
    changed: list[tuple[str, str, str, str]] = []
    for idx, line in enumerate(lines):
        key, value = _split(line)
        if not key or key not in HEAL_RULES:
            continue
        superseded, new_value, reason = HEAL_RULES[key]
        if value.lower() not in superseded or value == new_value:
            continue
        # Preserve the original indentation/spacing style of the line. Match the
        # KEY and the value as the operator typed them ("KEY = v" included); a
        # plain "=value" replace silently missed spaced assignments.
        new_line = re.sub(
            rf"^(\s*{re.escape(key)}\s*=\s*){re.escape(value)}",
            lambda m: m.group(1) + new_value,
            line,
            count=1,
            flags=re.IGNORECASE,
        )
        if new_line == line:
            continue  # could not rewrite it -- do not report it as healed
        lines[idx] = new_line
        changed.append((key, value, new_value, reason))
    return lines, changed


def _read_env(install_dir: Path, result: dict) -> tuple[Path, str | None]:
    """(path, text) of ``install_dir/.env``; text None when absent or too large."""
    env = Path(install_dir) / ".env"
    if not env.is_file():
        return env, None
    raw = env.read_text(encoding="utf-8")
    if len(raw.encode("utf-8", "ignore")) > _MAX_ENV_BYTES:
        result["error"] = "the .env is unexpectedly large -- not touching it"
        return env, None
    return env, raw


def find_superseded(install_dir: Path) -> dict:
    """What heal_env WOULD rewrite, without writing anything.

    Returns ``{"changed": [(key, old, new, reason), ...], "error": None}``, so a
    read-only qa-doctor can name outdated settings instead of skipping the
    check. Never raises.
    """
    result: dict = {"changed": [], "error": None}
    try:
        _env, raw = _read_env(install_dir, result)
        if raw is not None:
            result["changed"] = _scan(raw)[1]
        return result
    except Exception as exc:
        logger.exception("env superseded-value check failed")
        result["error"] = str(exc)
        return result


def _write_backup_and_replace(
    env: Path, backup: Path, raw: str, new_text: str, mode: int
) -> None:
    """Write the backup of *raw*, then atomically replace *env* with *new_text*."""
    # Backup carries the .env's own mode (it holds the same secrets).
    bfd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(bfd, "w", encoding="utf-8") as fh:
        fh.write(raw)
    # Atomic replace: a crash mid-write must not leave a truncated .env.
    tfd, tmp = tempfile.mkstemp(dir=str(env.parent), prefix=".env.tmp-")
    try:
        with os.fdopen(tfd, "w", encoding="utf-8") as fh:
            fh.write(new_text)
        os.chmod(tmp, mode)
        os.replace(tmp, env)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def heal_env(install_dir: Path) -> dict:
    """Rewrite superseded default values in ``install_dir/.env``.

    Returns ``{"changed": [(key, old, new, reason), ...], "backup": str,
    "error": None}``. ``changed`` is empty when there is nothing to do, which is
    the normal case. Never raises.
    """
    result: dict = {"changed": [], "backup": "", "error": None}
    try:
        env, raw = _read_env(install_dir, result)
        if raw is None:
            return result

        lines, changed = _scan(raw)
        if not changed:
            return result

        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = env.with_name(f".env.bak-{stamp}")
        trailing = "\n" if raw.endswith("\n") else ""
        new_text = "\n".join(lines) + trailing
        if new_text == raw:
            return result
        try:
            mode = env.stat().st_mode & 0o777
        except OSError:
            mode = 0o600
        _write_backup_and_replace(env, backup, raw, new_text, mode)
        result["changed"] = changed
        result["backup"] = str(backup)
        logger.info(
            "env self-heal rewrote %d superseded value(s); backup at %s",
            len(changed),
            backup,
        )
        return result
    except Exception as exc:
        logger.exception("env self-heal failed - leaving the .env untouched")
        result["error"] = str(exc)
        return result
