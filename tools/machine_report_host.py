"""The ``backend_version`` and ``host_privileges`` rows of the machine report."""

from __future__ import annotations

from tools import host_privileges
from tools.machine_report_core import _PROBE_ERRORS, Row, local_version

_REINSTALL = "Reinstall, or re-run the launcher."


# The ``backend_version`` row.
def version_row() -> Row:
    try:
        version = local_version()
        return Row(
            "backend_version",
            "ok" if version else "undetermined",
            str(version or "no version file in this install"),
            "" if version else _REINSTALL,
        )
    except _PROBE_ERRORS as exc:
        return Row("backend_version", "undetermined", str(exc), _REINSTALL)


def _privileges_detail(verdict: dict) -> str:
    unknown = bool(verdict.get("undetermined"))
    elevated = bool(verdict.get("can_elevate"))
    return (
        f"{verdict.get('os') or 'unknown os'} / {verdict.get('arch') or 'unknown arch'}: "
        + (
            "privilege state could not be determined"
            if unknown
            else ("can elevate" if elevated else "cannot elevate")
        )
        + (f" ({verdict['method']})" if verdict.get("method") else "")
    )


def _verdict_row(verdict: dict) -> Row:
    unknown = bool(verdict.get("undetermined"))
    elevated = bool(verdict.get("can_elevate"))
    return Row(
        "host_privileges",
        "undetermined" if unknown else ("ok" if elevated else "warn"),
        _privileges_detail(verdict),
        "" if elevated and not unknown else "Admin-free steps only on this account.",
    )


# The ``host_privileges`` row.
#
# probe() returns the tools/ envelope {"error", "content"}; the flags
# live INSIDE content. Reading them off the envelope made every machine
# 'undetermined' with a dict repr for a detail (measured on the bundled
# backend, 2026-09-16).
def privileges_row() -> Row:
    try:
        envelope = host_privileges.probe() or {}
        if envelope.get("error"):
            raise RuntimeError(str(envelope["error"]))
        return _verdict_row(envelope.get("content") or {})
    except _PROBE_ERRORS as exc:
        return Row("host_privileges", "undetermined", str(exc))
