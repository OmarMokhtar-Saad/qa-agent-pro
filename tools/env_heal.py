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
import os  # noqa: F401
import re
import tempfile  # noqa: F401
import time  # noqa: F401
from pathlib import Path

from tools.env_heal_write import commit

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

MAX_ENV_BYTES = 1_000_000


def _split(line: str) -> tuple[str, str]:
    """(KEY, value) for an ACTIVE assignment line, ('', '') otherwise."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return "", ""
    key, _, value = stripped.partition("=")
    return key.strip(), value.strip()


def _rewrite(line: str, key: str, edit: tuple[str, str]) -> str:
    """``line`` with ``edit`` = (value, new_value) applied to ``key``'s value."""
    value, new_value = edit
    # Preserve the original indentation/spacing style of the line. Match the
    # KEY and the value as the operator typed them ("KEY = v" included); a
    # plain "=value" replace silently missed spaced assignments.
    return re.sub(
        rf"^(\s*{re.escape(key)}\s*=\s*){re.escape(value)}",
        lambda m: m.group(1) + new_value,
        line,
        count=1,
        flags=re.IGNORECASE,
    )


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
        new_line = _rewrite(line, key, (value, new_value))
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
    if len(raw.encode("utf-8", "ignore")) > MAX_ENV_BYTES:
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


def _join(raw: str, lines: list[str]) -> str:
    """``lines`` re-joined, keeping ``raw``'s trailing newline (or lack of one)."""
    trailing = "\n" if raw.endswith("\n") else ""
    return "\n".join(lines) + trailing


def _heal(install_dir: Path, result: dict) -> None:
    """Rewrite the superseded values in ``install_dir/.env``, filling ``result``."""
    env, raw = _read_env(install_dir, result)
    if raw is None:
        return
    lines, changed = _scan(raw)
    if not changed:
        return
    new_text = _join(raw, lines)
    if new_text == raw:
        return
    backup = commit(env, raw, new_text)
    result["changed"] = changed
    result["backup"] = str(backup)
    logger.info(
        "env self-heal rewrote %d superseded value(s); backup at %s",
        len(changed),
        backup,
    )


# Rewrite superseded default values in ``install_dir/.env``.
#
# Returns ``{"changed": [(key, old, new, reason), ...], "backup": str,
# "error": None}``. ``changed`` is empty when there is nothing to do, which is
# the normal case. Never raises.
def heal_env(install_dir: Path) -> dict:
    result: dict = {"changed": [], "backup": "", "error": None}
    try:
        _heal(install_dir, result)
    except Exception as exc:
        logger.exception("env self-heal failed - leaving the .env untouched")
        result["error"] = str(exc)
    return result
