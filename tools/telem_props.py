"""Telemetry identity and base-property helpers (read patchable state via host())."""

from __future__ import annotations

import logging
import platform
import re
import uuid
from pathlib import Path
from typing import Any, cast

from config.settings_guard import never_raise
from tools import settings_port
from tools.telem_host import host

# py-machineid is optional: without it the machine id is ''.
try:
    import machineid
except ImportError as _exc:
    if _exc.name != "machineid":
        raise
    machineid = cast("Any", None)

logger = logging.getLogger("qa_agents.telemetry")

_MACHINE_ID_APP = "qa-agent-pro"


def _dist_version() -> str:
    """Best-effort app version from VERSION (dist) or pyproject.toml (checkout)."""
    try:
        install_dir = host().install_dir()
        vfile = install_dir / "VERSION"
        if vfile.is_file():
            v = vfile.read_text(encoding="utf-8").strip()
            if v:
                return v
        pp = install_dir / "pyproject.toml"
        if pp.is_file():
            m = re.search(
                r'(?m)^\s*version\s*=\s*"([^"]+)"', pp.read_text(encoding="utf-8")
            )
            if m:
                return m.group(1).strip()
    except OSError:
        pass
    return "unknown"


def _machine_id() -> str:
    """Anonymous per-app hashed machine id via optional ``py-machineid``.
    Returns '' when the package is absent or errors (graceful)."""
    # optional third-party package, any failure -> ''
    with never_raise():
        return str(machineid.hashed_id(_MACHINE_ID_APP))
    return ""


def _install_id() -> str:
    """Random install UUID, generated once and persisted under the
    (update-protected) data dir. Never raises."""
    path = Path(settings_port.get().qa_telemetry_id_path)
    try:
        if path.is_file():
            existing = path.read_text(encoding="utf-8").strip()
            if existing:
                return existing
    except OSError:
        logger.debug("telemetry: could not read install id", exc_info=True)
    new_id = uuid.uuid4().hex
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new_id, encoding="utf-8")
    except OSError:
        logger.debug("telemetry: could not persist install id", exc_info=True)
    return new_id


def _email() -> str:
    """The configured QA_USER_EMAIL, stripped; '' when unset or unreadable."""
    # never-raise: settings must not break telemetry
    with never_raise():
        return (settings_port.get().qa_user_email or "").strip()
    return ""


def _distinct_id() -> str:
    """Person key: the configured email when set, else the install UUID."""
    return _email() or host().install_id()


def _base_properties(client_name: str = "", client_version: str = "") -> dict:
    """Build the common event properties (best-effort)."""
    props: dict[str, Any] = {
        "dist_version": host()._dist_version(),
        "platform": f"{platform.system()} {platform.release()}".strip(),
        "arch": platform.machine(),
        "$lib": "qa-agent-pro-telemetry",
    }
    mid = host()._machine_id()
    if mid:
        props["machine_id"] = mid
    if client_name:
        props["mcp_client"] = client_name
    if client_version:
        props["mcp_client_version"] = client_version
    email = _email()
    if email:
        props["$set"] = {"email": email}
    return props
