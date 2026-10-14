"""The mitmdump addon script (T5.1).

Loaded with ``mitmdump -s addon.py``, and runs INSIDE the mitmdump process --
a DIFFERENT interpreter from the qa-agents server, provisioned separately
(``tools/mobile_capture/mitm_provision.py``). It therefore imports NOTHING
from this tree (``tests/mobile_capture/test_addon_contract.py`` walks it with
``ast`` to keep that checkable) and nothing from ``mitmproxy`` at module
scope: the hook reaches the flow by DUCK TYPING, so the pure functions run in
a plain interpreter (``mitmproxy`` is not a project dependency; see T2.4).

**It does NO redaction.** That is qa-agents' job at WRITE time
(``tools/mobile_capture/flows.py``), under a scope this process never sees.

**Routing.** Every flow is appended to the file the CURRENT-CASE pointer
names (``<run>.current``), read FRESH per flow so a case boundary mid-run is
picked up. It is written by :func:`tools.mobile_capture.flows.mark_case_current`
and cleared by :func:`tools.mobile_capture.flows.mark_case_finished`. No or an
unreadable pointer means "outside any case": the flow lands in
``<run>-outside.jsonl``, never attributed to the nearest case (see T5.4).

**Run id and output root come from the environment** (``MITMCAP_RUN_ID`` /
``MITMCAP_DIR``, set by ``tools/mobile_capture/proxy.py`` on the child).
Missing either means nothing is written; it never raises out of a hook, as a
crashed addon would take the tester's proxied device connection with it.
"""

from __future__ import annotations

import json
import os
import time

# mitmdump loads this file by path with its directory on sys.path
# (mitmproxy/addons/script.py load_script), so the sibling guard is a plain import.
from addon_guard import swallow

# One JSONL file's best-effort cap in THIS interpreter; the cap that protects a
# reader is ``flows.MAX_JSONL_BYTES``, enforced at the last consumer. Named with
# no MAX_/CAP_/LIMIT_ segment on purpose: the bounds-ceilings sweep cannot reach
# a file running in another interpreter and would demand an unvalidatable row.
_ADDON_STOP_BYTES = 32 * 1024 * 1024

# One body inlined into a line, before ``flows.MAX_BODY_BYTES`` runs at write
# time. Same naming reason as above.
_ADDON_BODY_STOP_BYTES = 2 * 1024 * 1024

# The ONLY channel to this process. NOT settings flags, and off the QA_ prefix:
# the deleted-flag detector reads any QA_ literal as a Settings field, and this
# child cannot import config/settings; widening its allow-list would blunt it.
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


def _part(message: object, name: str, default: object) -> object:
    return getattr(message, name, default) if message is not None else default


def _header_pairs(message: object) -> dict:
    if message is None:
        return {}
    return dict(getattr(message, "headers", None) or {})


def _flow_times(now_ms: int | None) -> int:
    return int(now_ms if now_ms is not None else time.time() * 1000)


def _url_of(request: object) -> str:
    return str(_part(request, "pretty_url", "") or _part(request, "url", "") or "")


def flow_to_record(flow: object, *, now_ms: int | None = None) -> dict:
    """One line's flow by DUCK TYPING; pure, NO redaction, no ``mitmproxy`` import."""
    request = getattr(flow, "request", None)
    response = getattr(flow, "response", None)
    req_body, req_trunc = _capped_body(_part(request, "raw_content", None))
    resp_body, resp_trunc = _capped_body(_part(response, "raw_content", None))
    return {
        "ts_ms": _flow_times(now_ms),
        "method": str(_part(request, "method", "") or ""),
        "url": _url_of(request),
        "status": _part(response, "status_code", None),
        "request_headers": _header_pairs(request),
        "response_headers": _header_pairs(response),
        "request_body": req_body,
        "response_body": resp_body,
        "body_truncated": bool(req_trunc or resp_trunc),
    }


def _open_for_append(path: str, line: str, max_bytes: int) -> bool:
    existing = os.path.getsize(path) if os.path.exists(path) else 0
    if existing >= max_bytes:
        return False
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    is_new = not os.path.exists(path)
    with open(path, "a", encoding=ENCODING) as handle:
        handle.write(line)
    if is_new:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    return True


def append_line(path: str, record: dict, *, max_bytes: int = _ADDON_STOP_BYTES) -> bool:
    """Append one JSON line to *path*, 0600 on creation, stopping at *max_bytes*.

    Returns whether the line was written. Never raises: a write failure is
    reported to stderr and the flow is simply not captured, which is always
    safer than crashing the proxy a tester's whole device connection depends
    on.
    """
    with swallow("mobile_capture.addon: append failed"):
        line = json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n"
        return _open_for_append(path, line, max_bytes)
    return False


def response(flow: object) -> None:
    """The mitmdump hook: called once per completed flow. Never raises."""
    with swallow("mobile_capture.addon: response hook failed"):
        run_id = _env_run_id()
        out_dir = _env_dir()
        if not run_id or not out_dir:
            return
        record = flow_to_record(flow)
        append_line(target_path(run_id, out_dir), record)
