"""Updater post-swap steps: .env migration and pip install (split from tools/updater.py)."""

from __future__ import annotations

import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from config.settings_guard import never_raise
from tools.process_runner import RunOpts, run
from tools.updater_log import logger
from tools.updater_version import _local_version

# Generous cap for the post-update dependency install (network + build).
_PIP_TIMEOUT = 600


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


# Run ``pip install -e .`` after a swap (deps may have changed). A failure
# is logged, not raised — startup proceeds on the newly-swapped code.
def _pip_install(install_dir: Path) -> None:
    """Run ``pip install -e .`` after a swap (deps may have changed).

    A failure is logged, not raised: startup proceeds on the swapped code.
    """
    argv = [sys.executable, "-m", "pip", "install", "-e", "."]
    pins = install_dir / "constraints.txt"
    if pins.is_file():
        argv += ["-c", str(pins)]
    result = run(argv, timeout=_PIP_TIMEOUT, opts=RunOpts(cwd=str(install_dir)))
    failure = result.exc
    if failure is None and result.rc:
        failure = subprocess.CalledProcessError(result.rc, argv)
    if failure is not None:
        logger.warning(
            "pip install -e . after update failed (%s) — dependencies may be stale.",
            failure,
        )


def _user_env_keys(lines: list) -> set:
    """Keys the user already has, active or commented out (an opt-out)."""
    keys = set()
    for line in lines:
        key = _env_line_key(line) or _commented_env_key(line)
        if key:
            keys.add(key)
    return keys


def _additions(example_text: str, user_keys: set) -> list:
    """Template lines whose key the user lacks (mutates *user_keys* to dedupe)."""
    additions = []
    for line in example_text.splitlines():
        key = _env_line_key(line)
        if key and key not in user_keys:
            additions.append(line.strip())
            user_keys.add(key)
    return additions


def _env_block(additions: list, install_dir: Path) -> list:
    """The header plus new lines appended to the user's ``.env``."""
    version = _local_version(install_dir) or "?"
    stamp = datetime.now().strftime("%Y-%m-%d")
    header = f"# --- new settings added by the v{version} update ({stamp}) ---"
    return ["", header] + additions


def _log_migrated(additions: list) -> None:
    """Log which settings were appended."""
    logger.info(
        "migrate_env: appended %d new setting(s) to .env: %s",
        len(additions),
        ", ".join(a.split("=", 1)[0] for a in additions),
    )


def _append_new_keys(install_dir: Path) -> int:
    """Append new template keys to ``.env``; returns the number added."""
    example = install_dir / ".env.example"
    env = install_dir / ".env"
    if not example.is_file() or not env.is_file():
        return 0
    user_lines = env.read_text(encoding="utf-8").splitlines()
    user_keys = _user_env_keys(user_lines)
    additions = _additions(example.read_text(encoding="utf-8"), user_keys)
    if not additions:
        return 0
    block = _env_block(additions, install_dir)
    env.write_text("\n".join(user_lines + block) + "\n", encoding="utf-8")
    _log_migrated(additions)
    return len(additions)


# Append config keys newly shipped in ``.env.example`` to the user's
# ``.env`` after an update. User lines are NEVER modified, reordered or
# removed; keys the user commented out count as present (deliberate
# opt-out); template-commented keys are docs and are not propagated.
# Returns the number of keys added; never raises.
def migrate_env(install_dir: Path) -> int:
    """Append newly shipped ``.env.example`` keys to ``.env``; never raises."""
    with never_raise(
        logger,
        "migrate_env failed — user .env left unchanged",
        level=logging.ERROR,
        exc_info=True,
    ):
        return _append_new_keys(install_dir)
    return 0
