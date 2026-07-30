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

import atexit
import contextvars
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
_DEFAULT_POSTHOG_KEY = "phc_your_key"

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
    extra: dict | None = None,
) -> None:
    """Emit a ``tool_called`` event. Never raises; carries only the exception
    CLASS name on failure (``error_type``), never message content. ``extra``
    holds content-free, handler-supplied properties (case_count / export format
    / source type) collected via the per-tool props bag."""
    props = _base_properties(client_name, client_version)
    props["tool"] = tool
    props["duration_ms"] = int(duration_ms)
    props["ok"] = bool(ok)
    if error_type:
        props["error_type"] = str(error_type)
    if extra:
        for key, value in extra.items():
            if value is not None:
                props[key] = value
    _capture("tool_called", props)


# --------------------------------------------------------------------------- #
# Dist-path SDK tracing: error tracking + $ai_generation (LLM analytics)
# --------------------------------------------------------------------------- #
# These extend the bare-HTTP dist telemetry above with the OPTIONAL ``posthog``
# SDK (issue grouping for error tracking) and PostHog LLM-analytics
# ``$ai_generation`` events. They gate on the DIST key (``_enabled`` /
# ``_api_key``). Every function is never-raise and content-free: exception MESSAGES and prompt /
# completion text are never transmitted.

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


def start_tool_trace(tool: str) -> str:
    """Begin a per-tool trace: set a fresh ``$ai_trace_id`` and an empty extra-
    properties bag so LLM generations during this call link to it and handlers
    can attach content-free properties. Returns the trace id. Never raises."""
    trace_id = uuid.uuid4().hex
    try:
        _ai_trace_id_var.set(trace_id)
        _tool_props_var.set({})
    except Exception:
        logger.debug("telemetry: start_tool_trace failed", exc_info=True)
    return trace_id


def current_trace_id() -> str:
    """The in-flight tool's ``$ai_trace_id`` ('' outside a tool call). Never raises."""
    try:
        return _ai_trace_id_var.get()
    except Exception:
        return ""


def add_tool_properties(**props) -> None:
    """Attach content-free properties (e.g. case_count / format / source) to the
    in-flight ``tool_called`` event. No-op outside a tool trace. Never raises."""
    try:
        bag = _tool_props_var.get()
        if bag is None:
            return
        for key, value in props.items():
            if value is not None:
                bag[key] = value
    except Exception:
        logger.debug("telemetry: add_tool_properties failed", exc_info=True)


def pop_tool_properties() -> dict:
    """Return and clear the in-flight tool's extra-properties bag. Never raises."""
    try:
        bag = _tool_props_var.get()
        _tool_props_var.set(None)
        return dict(bag) if bag else {}
    except Exception:
        return {}


def _scrub_event_paths(event):
    """posthog ``before_send`` hook for the dist client: strip absolute paths
    from crash stack frames (they can leak the user's OS username) and force
    each exception's ``value`` to its class name. Fail-CLOSED: if scrubbing
    errors, the event is DROPPED (return None), never sent unscrubbed.
    Empirically verified against posthog 7.29.0: this hook IS invoked for
    ``capture_exception`` ($exception) events; frames carry ``abs_path`` /
    ``filename`` keys."""
    try:
        props = event.get("properties") if isinstance(event, dict) else None
        exc_list = props.get("$exception_list") if isinstance(props, dict) else None
        if not exc_list:
            return event
        root = str(_INSTALL_DIR)
        for entry in exc_list:
            if not isinstance(entry, dict):
                continue
            if entry.get("type"):
                entry["value"] = str(entry["type"])
            frames = (entry.get("stacktrace") or {}).get("frames") or []
            for frame in frames:
                if not isinstance(frame, dict):
                    continue
                for key in ("abs_path", "filename"):
                    path = frame.get(key)
                    if not isinstance(path, str):
                        continue
                    if path.startswith(root):
                        frame[key] = path[len(root) :].lstrip("/\\")
                    else:
                        frame[key] = os.path.basename(path)
        return event
    except Exception:
        logger.debug(
            "telemetry: before_send scrub failed - dropping event", exc_info=True
        )
        return None


def _dist_sdk_client():
    """Cached ``posthog.Posthog`` bound to the DIST key (``_api_key``), or
    ``None`` when the SDK is absent / no key is set (bare-HTTP fallback).
    Independent of the app-analytics ``_sdk_client``. Never raises."""
    global _dist_sdk_cached
    if _dist_sdk_cached is not _dist_sdk_sentinel:
        return _dist_sdk_cached
    _dist_sdk_cached = None
    key = _api_key()
    if not key:
        return None
    try:
        from posthog import Posthog

        client = Posthog(
            project_api_key=key,
            host=_POSTHOG_HOST,
            enable_exception_autocapture=False,
            before_send=_scrub_event_paths,
        )
        atexit.register(_safe_flush, client)
        _dist_sdk_cached = client
    except Exception:
        logger.debug(
            "telemetry: dist posthog SDK unavailable -- bare-HTTP error events only",
            exc_info=True,
        )
        _dist_sdk_cached = None
    return _dist_sdk_cached


def _scrubbed_exception(exc: BaseException) -> BaseException:
    """Return an exception carrying the ORIGINAL traceback (stack frames drive
    issue grouping) but with the MESSAGE replaced by the class name only.

    External users' exception messages can embed feature text, URLs or secrets,
    so they must never be transmitted -- only the class name and the frames are.
    Never raises; returns the original exception if cloning fails for any reason.
    """
    try:
        cls = type(exc)
        try:
            scrubbed: BaseException = cls(cls.__name__)
        except Exception:
            scrubbed = Exception(cls.__name__)
        scrubbed.__traceback__ = getattr(exc, "__traceback__", None)
        return scrubbed
    except Exception:
        return exc


def capture_error_dist(
    exc: BaseException,
    *,
    tool: str | None = None,
    origin: str | None = None,
    properties: dict | None = None,
) -> None:
    """Report a dist-path exception to PostHog error tracking. Gated on the DIST
    key (``_enabled``). Privacy: only the exception
    CLASS name and stack FRAMES are sent -- the message and absolute frame
    paths are scrubbed (see ``_scrub_event_paths``). Never raises.
    With the SDK present it groups via ``capture_exception``; without it a
    best-effort bare-HTTP ``tool_error`` event (class name only) is sent."""
    if not _enabled():
        return
    try:
        props = _base_properties()
        props["error_type"] = type(exc).__name__
        if tool:
            props["tool"] = str(tool)
        if origin:
            props["origin"] = str(origin)
        tid = current_trace_id()
        if tid:
            props["$ai_trace_id"] = tid
        if properties:
            for key, value in properties.items():
                if value is not None:
                    props[key] = value
        client = _dist_sdk_client()
        if client is not None:
            client.capture_exception(
                _scrubbed_exception(exc),
                distinct_id=_distinct_id(),
                properties=props,
            )
        else:
            _dispatch(_build_payload("tool_error", _distinct_id(), props))
    except Exception:
        logger.debug("telemetry: capture_error_dist failed", exc_info=True)


def capture_ai_generation(
    *,
    model: str = "",
    provider: str = "",
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    latency_s: float | None = None,
    estimated: bool = False,
    trace_id: str | None = None,
    method: str = "",
    ok: bool = True,
    error_type: str | None = None,
) -> None:
    """Emit a PostHog LLM-analytics ``$ai_generation`` event for one LLM call.

    Content-free by contract: ``$ai_input`` / ``$ai_output_choices`` (prompt and
    completion text) are NEVER included -- only metadata (model, provider, token
    counts, latency, trace id). Gated on the DIST key (``_enabled``). Never raises.
    """
    if not _enabled():
        return
    try:
        props = _base_properties()
        props["$ai_model"] = str(model or "")
        props["$ai_provider"] = str(provider or "")
        if input_tokens is not None:
            props["$ai_input_tokens"] = int(input_tokens)
        if output_tokens is not None:
            props["$ai_output_tokens"] = int(output_tokens)
        if latency_s is not None:
            props["$ai_latency"] = round(float(latency_s), 3)
        props["estimated"] = bool(estimated)
        if method:
            props["method"] = str(method)
        props["ok"] = bool(ok)
        if error_type:
            props["error_type"] = str(error_type)
        tid = trace_id or current_trace_id()
        if tid:
            props["$ai_trace_id"] = tid
        _capture("$ai_generation", props)
    except Exception:
        logger.debug("telemetry: capture_ai_generation failed", exc_info=True)


def _safe_flush(client: object) -> None:
    """Flush a posthog client, swallowing any error (never raises)."""
    try:
        client.flush()
    except Exception:
        logger.debug("analytics: flush failed", exc_info=True)
