"""Anonymous, opt-out usage analytics for the public distribution (qa-agent-pro).

House-rule alignment: like ``tools/jira_fetcher`` this module follows a strict
never-raise contract; it imports nothing internal except ``config.settings``.
Transport is a plain HTTP POST to PostHog Cloud (no SDK). Fire-and-forget on a
daemon thread with a ~2s timeout; a failed send is dropped. Privacy: no PII
beyond an OPTIONAL ``QA_USER_EMAIL``; on failure only the exception class name
is recorded. Opt out with ``QA_TELEMETRY_DISABLED=1`` or ``DO_NOT_TRACK=1``.
Active only when a PostHog key is present AND no opt-out is set.
"""

from __future__ import annotations

import logging
import os
import platform
import re
import threading
import uuid
from pathlib import Path

import httpx

from config.settings import settings

logger = logging.getLogger("qa_agents.telemetry")

_POSTHOG_HOST = "https://us.i.posthog.com"
_CAPTURE_PATH = "/i/v0/e/"
_TIMEOUT_S = 2.0

# Baked by scripts/build_dist.py in the public distribution. Empty in the
# private checkout, which keeps telemetry inert (fail-safe OFF) there.
_DEFAULT_POSTHOG_KEY = "phc_Bpq6EEL6LeBRgAiQTamtsEz5icUEGoR6nFkKsnmv3BoX"

_MACHINE_ID_APP = "qa-agent-pro"
_INSTALL_DIR = Path(__file__).resolve().parent.parent
_TRUTHY = ("1", "true", "yes", "on")

_notice_logged = False


def _api_key() -> str:
    """Resolve the PostHog key: explicit env override, else the baked default."""
    try:
        return (settings.posthog_api_key or _DEFAULT_POSTHOG_KEY or "").strip()
    except Exception:
        return (_DEFAULT_POSTHOG_KEY or "").strip()


def _opted_out() -> bool:
    """True when the operator disabled telemetry (either flag)."""
    try:
        if settings.qa_telemetry_disabled:
            return True
    except Exception:
        pass
    return str(os.environ.get("DO_NOT_TRACK", "")).strip().lower() in _TRUTHY


def _enabled() -> bool:
    """Telemetry sends only with a key present AND no opt-out set."""
    return bool(_api_key()) and not _opted_out()


def startup_notice() -> None:
    """Log a one-line 'telemetry: on/off' disclosure once per process."""
    global _notice_logged
    if _notice_logged:
        return
    _notice_logged = True
    try:
        if not _api_key():
            logger.info("telemetry: off (no analytics key in this build)")
        elif _opted_out():
            logger.info(
                "telemetry: off (disabled via QA_TELEMETRY_DISABLED / DO_NOT_TRACK)"
            )
        else:
            logger.info(
                "telemetry: on - anonymous usage metrics; opt out with "
                "QA_TELEMETRY_DISABLED=1 or DO_NOT_TRACK=1"
            )
    except Exception:
        logger.debug("telemetry startup notice failed", exc_info=True)


def _dist_version() -> str:
    """Best-effort app version from VERSION (dist) or pyproject.toml (checkout)."""
    try:
        vfile = _INSTALL_DIR / "VERSION"
        if vfile.is_file():
            v = vfile.read_text(encoding="utf-8").strip()
            if v:
                return v
        pp = _INSTALL_DIR / "pyproject.toml"
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
    try:
        import machineid

        return str(machineid.hashed_id(_MACHINE_ID_APP))
    except Exception:
        return ""


def _install_id() -> str:
    """Random install UUID, generated once and persisted under the
    (update-protected) data dir. Never raises."""
    path = Path(settings.qa_telemetry_id_path)
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


def _distinct_id() -> str:
    """Person key: the configured email when set, else the install UUID."""
    try:
        email = (settings.qa_user_email or "").strip()
    except Exception:
        email = ""
    return email or _install_id()


def _base_properties(client_name: str = "", client_version: str = "") -> dict:
    """Build the common event properties (best-effort)."""
    props = {
        "dist_version": _dist_version(),
        "platform": f"{platform.system()} {platform.release()}".strip(),
        "arch": platform.machine(),
        "$lib": "qa-agent-pro-telemetry",
    }
    mid = _machine_id()
    if mid:
        props["machine_id"] = mid
    if client_name:
        props["mcp_client"] = client_name
    if client_version:
        props["mcp_client_version"] = client_version
    try:
        email = (settings.qa_user_email or "").strip()
    except Exception:
        email = ""
    if email:
        props["$set"] = {"email": email}
    return props


def _build_payload(event: str, distinct_id: str, properties: dict) -> dict:
    return {
        "api_key": _api_key(),
        "event": event,
        "distinct_id": distinct_id,
        "properties": properties,
    }


def _post(payload: dict) -> None:
    """Fire a single capture POST. Never raises (best-effort delivery)."""
    try:
        httpx.post(
            _POSTHOG_HOST + _CAPTURE_PATH,
            json=payload,
            timeout=_TIMEOUT_S,
            headers={"Content-Type": "application/json"},
        )
    except Exception:
        logger.debug("telemetry: capture POST failed", exc_info=True)


def _dispatch(payload: dict) -> None:
    """Send on a daemon thread so a slow network never delays a tool."""
    try:
        threading.Thread(target=_post, args=(payload,), daemon=True).start()
    except Exception:
        logger.debug("telemetry: could not dispatch event", exc_info=True)


def _capture(event: str, properties: dict) -> None:
    if not _enabled():
        return
    try:
        _dispatch(_build_payload(event, _distinct_id(), properties))
    except Exception:
        logger.debug("telemetry: capture failed", exc_info=True)


def server_start(client_name: str = "", client_version: str = "") -> None:
    """Emit a ``server_start`` event. Never raises."""
    _capture("server_start", _base_properties(client_name, client_version))


def tool_called(
    tool: str,
    *,
    duration_ms: int = 0,
    ok: bool = True,
    error_type=None,
    client_name: str = "",
    client_version: str = "",
) -> None:
    """Emit a ``tool_called`` event. Never raises; carries only the exception
    CLASS name on failure (``error_type``), never message content."""
    props = _base_properties(client_name, client_version)
    props["tool"] = tool
    props["duration_ms"] = int(duration_ms)
    props["ok"] = bool(ok)
    if error_type:
        props["error_type"] = str(error_type)
    _capture("tool_called", props)
