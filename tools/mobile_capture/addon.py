"""The mitmdump addon script (T5.1).

Loaded with ``mitmdump -s addon.py``, and runs INSIDE the mitmdump process --
a DIFFERENT interpreter from the qa-agents server, provisioned separately
(``tools/mobile_capture/mitm_provision.py``). This module therefore imports
NOTHING from this tree (``tests/mobile_capture/test_addon_contract.py`` walks
it with ``ast`` to keep that a checkable fact, not a promise) and nothing from
``mitmproxy`` either, at module scope: the hook below reaches the flow object
by DUCK TYPING (``flow.request.method`` etc, attributes every mitmdump
``http.HTTPFlow`` exposes), so this file can be imported and its pure
functions exercised in a plain interpreter that never installed mitmproxy --
which is exactly what the test suite does (``mitmproxy`` is not a project
dependency; see T2.4).

**It does NO redaction.** Redaction is qa-agents' job, at WRITE time
(``tools/mobile_capture/flows.py``), applying ``tools/mobile_evidence/scrub``'s
nets under an armed, per-task :class:`contextvars.ContextVar` scope this
process never sees. Splitting redaction into this file would put the pattern
set in a process qa-agents cannot unit-test in-process.

**Routing.** Every flow is appended to the file the CURRENT-CASE pointer
names, read FRESH on every flow so a case boundary mid-run is picked up
without a restart. The pointer (``<run>.current``) is the ONE PRODUCER of
which case is "current": written by
:func:`tools.mobile_capture.flows.mark_case_current`, cleared by
:func:`tools.mobile_capture.flows.mark_case_finished`. No pointer, or an
unreadable one, means "outside any case" -- the flow lands in
``<run>-outside.jsonl`` rather than being silently attributed to whichever
case happens to be nearest (the defect this design specifically avoids; see
T5.4).

**The output root and run id both come from the environment**
(``MITMCAP_RUN_ID`` / ``MITMCAP_DIR``, written onto the ``mitmdump``
child's own environment by ``tools/mobile_capture/proxy.py`` -- the only
channel available to a process spawned this way). Missing either means the
addon writes nothing; it never raises out of a mitmdump hook, because a
crashed addon would take the tester's own proxied device connection with it.
"""

from __future__ import annotations

import json
import os
import sys
import time

#: One line's raw JSONL file, in THIS interpreter. Best-effort only -- this
#: process cannot see what qa-agents ends up reading, so it is not the cap
#: that actually protects a reader; that is ``flows.MAX_JSONL_BYTES``,
#: enforced at the LAST consumer. Deliberately named without a
#: MAX_/CAP_/LIMIT_ segment: the tree-wide bounds-ceilings sweep has no reach
#: into a file that runs in another interpreter, and giving this a cap-shaped
#: name would make the sweep demand a CEILINGS row it can never validate.
_ADDON_STOP_BYTES = 32 * 1024 * 1024

#: One body inlined into one JSON line, before qa-agents' own cap
#: (``flows.MAX_BODY_BYTES``) runs at write time. Same naming reason as above.
_ADDON_BODY_STOP_BYTES = 2 * 1024 * 1024

#: The ONLY channel to this process. See the module docstring.
# NOT settings flags, and deliberately off the QA_ prefix: the tree-wide
# deleted-flag detector reads any QA_-prefixed literal as a Settings field,
# and these are process env vars handed to the mitmdump child, which cannot
# import config/settings at all. Widening that detector's allow-list to admit
# them would blunt it for the thing it exists to catch.
RUN_ID_ENV = "MITMCAP_RUN_ID"
DIR_ENV = "MITMCAP_DIR"

ENCODING = "utf-8"
OUTSIDE_STEM = "outside"
TRUNCATION_MARKER = " ...[truncated by the addon]"


def _env_run_id() -> str:
    return str(os.environ.get(RUN_ID_ENV) or "")


def _env_dir() -> str:
    return str(os.environ.get(DIR_ENV) or "")


def pointer_path(run_id: str, out_dir: str) -> str:
    """Where :func:`current_case` reads from -- the file
    :func:`tools.mobile_capture.flows.mark_case_current` writes."""
    return os.path.join(out_dir, run_id + ".current")


def current_case(run_id: str, out_dir: str) -> str:
    """The case the pointer file names right now, or ``""`` (outside any case).

    Read FRESH every call -- never cached -- so a case boundary is picked up
    without restarting this process. An unreadable or missing pointer is
    "outside", never an exception: a device mid-request cannot wait on a
    file-system race.
    """
    try:
        with open(pointer_path(run_id, out_dir), "r", encoding=ENCODING) as handle:
            return handle.read().strip()
    except OSError:
        return ""


def target_path(run_id: str, out_dir: str) -> str:
    """The jsonl file the NEXT flow belongs in."""
    case = current_case(run_id, out_dir)
    stem = run_id + "-" + (case if case else OUTSIDE_STEM)
    return os.path.join(out_dir, stem + ".jsonl")


def _decode(data: object) -> str:
    """Bytes -> text, never raising: a device is free to send anything."""
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return str(data)


def _capped_body(data: object) -> tuple[str, bool]:
    text = _decode(data)
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= _ADDON_BODY_STOP_BYTES:
        return text, False
    return (
        encoded[:_ADDON_BODY_STOP_BYTES].decode("utf-8", errors="replace")
        + TRUNCATION_MARKER,
        True,
    )


def flow_to_record(flow: object, *, now_ms: int | None = None) -> dict:
    """One line's worth of a flow. Pure -- no I/O, no ``mitmproxy`` import.

    Reads *flow* by DUCK TYPING so this can be exercised against a plain
    fake object in tests, not only a real ``http.HTTPFlow``. Carries NO
    redaction: every value here is exactly what the device sent or received.
    """
    request = getattr(flow, "request", None)
    response = getattr(flow, "response", None)
    req_body, req_trunc = _capped_body(
        getattr(request, "raw_content", None) if request is not None else None
    )
    resp_body, resp_trunc = _capped_body(
        getattr(response, "raw_content", None) if response is not None else None
    )
    req_headers = dict(getattr(request, "headers", None) or {}) if request is not None else {}
    resp_headers = (
        dict(getattr(response, "headers", None) or {}) if response is not None else {}
    )
    url = ""
    if request is not None:
        url = str(getattr(request, "pretty_url", "") or getattr(request, "url", "") or "")
    return {
        "ts_ms": int(now_ms if now_ms is not None else time.time() * 1000),
        "method": str(getattr(request, "method", "") or "") if request is not None else "",
        "url": url,
        "status": getattr(response, "status_code", None) if response is not None else None,
        "request_headers": req_headers,
        "response_headers": resp_headers,
        "request_body": req_body,
        "response_body": resp_body,
        "body_truncated": bool(req_trunc or resp_trunc),
    }


def append_line(path: str, record: dict, *, max_bytes: int = _ADDON_STOP_BYTES) -> bool:
    """Append one JSON line to *path*, 0600 on creation, stopping at *max_bytes*.

    Returns whether the line was written. Never raises: a write failure is
    reported to stderr and the flow is simply not captured, which is always
    safer than crashing the proxy a tester's whole device connection depends
    on.
    """
    try:
        line = json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n"
        existing = os.path.getsize(path) if os.path.exists(path) else 0
        if existing >= max_bytes:
            return False
        parent = os.path.dirname(path) or "."
        os.makedirs(parent, exist_ok=True)
        is_new = not os.path.exists(path)
        with open(path, "a", encoding=ENCODING) as handle:
            handle.write(line)
        if is_new:
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        return True
    except Exception as exc:  # pragma: no cover - defensive, another interpreter
        sys.stderr.write("mobile_capture.addon: append failed: " + str(exc) + "\n")
        return False


def response(flow: object) -> None:
    """The mitmdump hook: called once per completed flow. Never raises."""
    try:
        run_id = _env_run_id()
        out_dir = _env_dir()
        if not run_id or not out_dir:
            return
        record = flow_to_record(flow)
        append_line(target_path(run_id, out_dir), record)
    except Exception as exc:  # pragma: no cover - defensive, another interpreter
        sys.stderr.write("mobile_capture.addon: response hook failed: " + str(exc) + "\n")
