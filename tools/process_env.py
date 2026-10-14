"""Process-environment port: the one place that reads or writes ``os.environ``
and resolves the home directory. Stdlib-only leaf (see ``stdlib-leaves`` in
.importlinter): it imports no project module.

The function names contain ``_env`` on purpose. ``tools/selfcheck.py``
justifies a LIVE_ENV name by finding it within 120 characters after the regex
``environ|getenv|_setting|_env``; callers keep the variable name as a literal at
the call site, e.g. ``get_env_str("QA_STAGING_DIR")``.

Nothing is cached: every call reads the live environment, so tests that patch
the environment or ``Path.home`` keep working. ``home_dir`` never catches;
callers decide what to do when there is no home."""

from __future__ import annotations

import os
from pathlib import Path
from typing import overload


@overload
def get_env(name: str) -> str | None: ...
@overload
def get_env(name: str, default: str) -> str: ...


def get_env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name, default)


def get_env_str(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def set_env(name: str, value: str) -> None:
    os.environ[name] = value


def setdefault_env(name: str, value: str) -> str:
    return os.environ.setdefault(name, value)


def snapshot_env() -> dict[str, str]:
    return dict(os.environ)


def restore_env(snapshot: dict[str, str]) -> None:
    os.environ.clear()
    os.environ.update(snapshot)


def home_dir() -> Path:
    return Path.home()
