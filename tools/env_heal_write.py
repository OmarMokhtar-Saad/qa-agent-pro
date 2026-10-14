"""The write half of the .env self-heal: backup first, then an atomic replace."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path


def write_backup(backup: Path, raw: str, mode: int) -> None:
    """Write the backup of *raw*, carrying the .env's own mode."""
    # Backup carries the .env's own mode (it holds the same secrets).
    bfd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(bfd, "w", encoding="utf-8") as fh:
        fh.write(raw)


def replace_env(env: Path, new_text: str, mode: int) -> None:
    """Atomically replace *env* with *new_text*."""
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


def commit(env: Path, raw: str, new_text: str) -> Path:
    """Back *raw* up beside *env*, replace *env* with *new_text*; the backup path."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = env.with_name(f".env.bak-{stamp}")
    try:
        mode = env.stat().st_mode & 0o777
    except OSError:
        mode = 0o600
    write_backup(backup, raw, mode)
    replace_env(env, new_text, mode)
    return backup
