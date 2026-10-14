"""Parameter objects of tools.client_registry (split out of client_registry.py)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from tools.clireg_consts import SERVER_NAME


@dataclass(frozen=True)
class EntrySpec:
    """One MCP entry to merge into one config file (``register_entry``)."""

    config_path: Path
    server_name: str
    entry: dict
    insert_only: bool = False
    require_dir: Path | None = None


@dataclass(frozen=True)
class ClientOpts:
    """The optional knobs of ``register_client``."""

    server_name: str = SERVER_NAME
    insert_only: bool = False
    require_dir: Path | None = None


class _Src(NamedTuple):
    """One config file to scan: its path, scope and workspace base."""

    path: Path
    scope: str
    base: object = None
