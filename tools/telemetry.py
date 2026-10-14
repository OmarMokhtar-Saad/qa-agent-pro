"""Anonymous, opt-out usage analytics for the public distribution (qa-agent-pro).

House-rule alignment: like ``tools/jira_fetcher`` this module follows a strict
never-raise contract; it imports nothing internal except ``config.settings``
and the stdlib-only leaf ``tools.process_env``.
Transport is a plain HTTP POST to PostHog Cloud (no SDK). Fire-and-forget on a
daemon thread with a ~2s timeout; a failed send is dropped. Privacy: no PII
beyond an OPTIONAL ``QA_USER_EMAIL``; on failure only the exception class name
is recorded. Opt out with ``DO_NOT_TRACK=1`` -- the cross-vendor standard, and
since 2026-08-13 the ONLY opt-out: ``QA_TELEMETRY_DISABLED`` was DELETED
(flag-surface reduction, batch 6) and hardcoded to its default, False.
Active only when a PostHog key is present AND no opt-out is set.

The emitters, identity, scrubbing and trace helpers live in the ``telem_*``
leaves; they read this module's patchable names through ``telem_host.host()``.
"""

from __future__ import annotations

import atexit  # noqa: F401
import contextvars
import logging
import sys
import threading
from pathlib import Path

from config.settings_guard import never_raise
from tools import http_port, settings_port, telem_host
from tools.process_env import get_env
from tools.telem_args import AiGeneration, ErrorReport, ToolCall  # noqa: F401
from tools.telem_emit import (  # noqa: F401
    capture_ai_generation,
    capture_error_dist,
    tool_called,
)
from tools.telem_props import (  # noqa: F401
    _MACHINE_ID_APP,
    _base_properties,
    _dist_version,
    _distinct_id,
    _machine_id,
)
from tools.telem_props import _install_id as _real_install_id
from tools.telem_scrub import (  # noqa: F401
    _safe_flush,
    _scrub_event_paths,
    _scrub_exception_entry,
    _scrub_frame_paths,
    _scrubbed_exception,
    build_dist_client,
)
from tools.telem_trace import (  # noqa: F401
    add_tool_properties,
    current_trace_id,
    pop_tool_properties,
    start_tool_trace,
)

logger = logging.getLogger("qa_agents.telemetry")

_POSTHOG_HOST = "https://us.i.posthog.com"
_CAPTURE_PATH = "/i/v0/e/"
_TIMEOUT_S = 2.0

# Baked by scripts/build_dist.py in the public distribution. Empty in the
# private checkout, which keeps telemetry inert (fail-safe OFF) there.
_DEFAULT_POSTHOG_KEY = "phc_Bpq6EEL6LeBRgAiQTamtsEz5icUEGoR6nFkKsnmv3BoX"

_INSTALL_DIR = Path(__file__).resolve().parent.parent


def install_dir() -> Path:
    """The install root, read at call time (tests patch this accessor)."""
    return _INSTALL_DIR


def posthog_key() -> str:
    """The key baked by build_dist, read at call time."""
    return _DEFAULT_POSTHOG_KEY


_TRUTHY = ("1", "true", "yes", "on")

_notice_logged = False

# Per-MCP-tool-invocation trace id + an extra-properties bag the handlers fill
# (case_count / export format / source type). Set in mcp_server._tracked; the
# handler body runs in the same task context and mutates the SAME bag object,
# which _tracked reads back in its finally to enrich the tool_called event.
_ai_trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "qa_ai_trace_id", default=""
)
_tool_props_var: contextvars.ContextVar = contextvars.ContextVar(
    "qa_tool_props", default=None
)

_dist_sdk_sentinel = object()
_dist_sdk_cached: object = _dist_sdk_sentinel


def api_key() -> str:
    """Resolve the PostHog key: explicit env override, else the baked default."""
    with never_raise():
        return (settings_port.get().posthog_api_key or posthog_key() or "").strip()
    return (posthog_key() or "").strip()


def _opted_out() -> bool:
    """True when the operator disabled telemetry (DO_NOT_TRACK is the only switch)."""
    return str(get_env("DO_NOT_TRACK", "")).strip().lower() in _TRUTHY


def enabled() -> bool:
    """Telemetry sends only with a key present AND no opt-out set."""
    return bool(api_key()) and not _opted_out()


def install_id() -> str:
    """The anonymous per-install id (the seam tests and the leaves read)."""
    return _real_install_id()


def reset_startup_notice() -> None:
    """Allow ``startup_notice`` to log again (tests)."""
    global _notice_logged
    _notice_logged = False


def startup_notice() -> None:
    """Log a one-line 'telemetry: on/off' disclosure once per process."""
    global _notice_logged
    if _notice_logged:
        return
    _notice_logged = True
    with never_raise(logger, "telemetry startup notice failed", exc_info=True):
        if not api_key():
            logger.info("telemetry: off (no analytics key in this build)")
        elif _opted_out():
            logger.info("telemetry: off (disabled via DO_NOT_TRACK)")
        else:
            logger.info(
                "telemetry: on - anonymous usage metrics; opt out with DO_NOT_TRACK=1"
            )


def build_payload(event: str, distinct_id: str, properties: dict) -> dict:
    return {
        "api_key": api_key(),
        "event": event,
        "distinct_id": distinct_id,
        "properties": properties,
    }


def post(payload: dict) -> None:
    """Fire a single capture POST. Never raises (best-effort delivery)."""
    with never_raise(logger, "telemetry: capture POST failed", exc_info=True):
        http_port.post_json(_POSTHOG_HOST + _CAPTURE_PATH, payload, _TIMEOUT_S)


def dispatch(payload: dict) -> None:
    """Send on a daemon thread so a slow network never delays a tool."""
    with never_raise(logger, "telemetry: could not dispatch event", exc_info=True):
        threading.Thread(target=post, args=(payload,), daemon=True).start()


def capture(event: str, properties: dict) -> None:
    if not enabled():
        return
    with never_raise(logger, "telemetry: capture failed", exc_info=True):
        dispatch(build_payload(event, _distinct_id(), properties))


def server_start(client_name: str = "", client_version: str = "") -> None:
    """Emit a ``server_start`` event. Never raises."""
    capture("server_start", _base_properties(client_name, client_version))


def dist_sdk_client():
    """Cached ``posthog.Posthog`` bound to the DIST key (``api_key``), or
    ``None`` when the SDK is absent / no key is set (bare-HTTP fallback).
    Independent of the app-analytics ``_sdk_client``. Never raises."""
    global _dist_sdk_cached
    if _dist_sdk_cached is not _dist_sdk_sentinel:
        return _dist_sdk_cached
    _dist_sdk_cached = None
    key = api_key()
    if key:
        _dist_sdk_cached = build_dist_client(key, _POSTHOG_HOST)
    return _dist_sdk_cached


telem_host.bind(sys.modules[__name__])
