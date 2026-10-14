"""Crash-report scrubbing and the dist posthog client (never raise)."""

from __future__ import annotations

import atexit
import logging
import os
from typing import Any, cast

from config.settings_guard import never_raise
from tools.telem_host import host

# The posthog SDK is optional: without it only bare-HTTP error events are sent.
try:
    import posthog
except ImportError as _exc:
    if _exc.name != "posthog":
        raise
    posthog = cast("Any", None)

logger = logging.getLogger("qa_agents.telemetry")


def _scrub_frame_paths(frame: dict, root: str) -> None:
    """Make a stack frame's ``abs_path`` / ``filename`` install-relative or a bare name."""
    for key in ("abs_path", "filename"):
        path = frame.get(key)
        if not isinstance(path, str):
            continue
        if path.startswith(root):
            frame[key] = path[len(root) :].lstrip("/\\")
        else:
            frame[key] = os.path.basename(path)


def _scrub_exception_entry(entry: dict, root: str) -> None:
    """Force the entry's ``value`` to its type name and scrub every frame path."""
    if entry.get("type"):
        entry["value"] = str(entry["type"])
    frames = (entry.get("stacktrace") or {}).get("frames") or []
    for frame in frames:
        if isinstance(frame, dict):
            _scrub_frame_paths(frame, root)


def _exception_list(event):
    """The event's ``$exception_list`` (None when absent or malformed)."""
    props = event.get("properties") if isinstance(event, dict) else None
    return props.get("$exception_list") if isinstance(props, dict) else None


# posthog ``before_send`` hook for the dist client: strip absolute paths
# from crash stack frames (they can leak the user's OS username) and force
# each exception's ``value`` to its class name. Fail-CLOSED: if scrubbing
# errors, the event is DROPPED (return None), never sent unscrubbed.
# Empirically verified against posthog 7.29.0: this hook IS invoked for
# ``capture_exception`` ($exception) events; frames carry ``abs_path`` /
# ``filename`` keys.
def _scrub_event_paths(event):
    """posthog ``before_send`` hook: scrub crash-frame paths, fail-closed."""
    # fail-closed: drop the event, never raise
    with never_raise(
        logger, "telemetry: before_send scrub failed - dropping event", exc_info=True
    ):
        exc_list = _exception_list(event)
        if not exc_list:
            return event
        root = str(host().install_dir())
        for entry in exc_list:
            if isinstance(entry, dict):
                _scrub_exception_entry(entry, root)
        return event
    return None


def _scrubbed_exception(exc: BaseException) -> BaseException:
    """Return an exception carrying the ORIGINAL traceback (stack frames drive
    issue grouping) but with the MESSAGE replaced by the class name only.

    External users' exception messages can embed feature text, URLs or secrets,
    so they must never be transmitted -- only the class name and the frames are.
    Never raises; returns the original exception if cloning fails for any reason.
    """
    try:
        cls = type(exc)
        # arbitrary exception constructors
        with never_raise() as g:
            scrubbed: BaseException = cls(cls.__name__)
        if g.failed:
            scrubbed = Exception(cls.__name__)
        scrubbed.__traceback__ = getattr(exc, "__traceback__", None)
        return scrubbed
    except (TypeError, AttributeError):
        return exc


def _safe_flush(client: Any) -> None:
    """Flush a posthog client, swallowing any error (never raises)."""
    # never-raise: runs at interpreter exit
    with never_raise(logger, "analytics: flush failed", exc_info=True):
        client.flush()


def build_dist_client(key: str, host_url: str):
    """A ``posthog.Posthog`` bound to *key*, or None when the SDK is unusable."""
    # optional SDK: any init failure -> bare HTTP
    with never_raise(
        logger,
        "telemetry: dist posthog SDK unavailable -- bare-HTTP error events only",
        exc_info=True,
    ):
        client = posthog.Posthog(
            project_api_key=key,
            host=host_url,
            enable_exception_autocapture=False,
            before_send=_scrub_event_paths,
        )
        atexit.register(_safe_flush, client)
        return client
    return None
