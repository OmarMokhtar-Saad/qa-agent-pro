"""The host steps of the preflight: free disk, cache ownership, privileges."""

from __future__ import annotations

import logging

from tools import host_privileges
from tools.mobile import downloader, paths
from tools.mobile import preflight_rows as rows
from tools.mobile.preflight_consts import _STEP_ERRORS, RUN_FREE_BYTES
from tools.mobile.preflight_rows import _failed

logger = logging.getLogger("tools.mobile.preflight")


def _disk_row() -> dict:
    """The single ``free_disk`` row read from the downloader's disk check."""
    disk = (downloader.check_disk(RUN_FREE_BYTES, paths.sub("runs")) or {}).get(
        "content"
    ) or {}
    fix = (
        ""
        if disk.get("ok")
        else "Free some space on the volume holding "
        + str(paths.cache_root())
        + " and try again."
    )
    return rows.record_row(
        "free_disk", rows._Row(bool(disk.get("ok")), str(disk.get("detail") or ""), fix)
    )


# 10. free disk.
def _step_free_disk(checks: list[dict]) -> None:
    """10. free disk."""
    try:
        checks.append(_disk_row())
    except _STEP_ERRORS as exc:
        checks.append(_failed("free_disk", exc))


def _ownership_row() -> dict:
    """The single ``cache_ownership`` row read from ``paths.ownership``."""
    owned = (paths.ownership() or {}).get("content") or {}
    checked = "" if bool(owned.get("checked")) else " [not verified on this host]"
    return rows.record_row(
        "cache_ownership",
        rows._Row(
            bool(owned.get("ok")),
            str(owned.get("detail") or "") + checked,
            str(owned.get("fix") or ""),
        ),
    )


# 11. cache ownership.
#
# ONE PRODUCER, ONE MEANING, EVERY CONSUMER. `paths.ownership()` has
# produced a `checked` field since Phase 5 and NOTHING read it: both
# consumers branch on `ok` alone, so on Windows -- where a POSIX uid
# comparison carries no meaning, and the function therefore answers
# `ok=True, checked=False` -- the disclosure that distinguishes "checked
# and fine" from "not checked at all" reached no tester, while
# `paths.ownership`'s own docstring claimed the docs repeated it. This is
# the consumer that renders it, and the bracketed suffix is what makes
# the two states distinguishable in the tester-facing line.
def _step_cache_ownership(checks: list[dict]) -> None:
    """11. cache ownership."""
    try:
        checks.append(_ownership_row())
    except _STEP_ERRORS as exc:
        checks.append(_failed("cache_ownership", exc))


_PRIVILEGE_FIX = (
    "Nothing here is blocked. Prefer the admin-free "
    "install route, and call qa_host_check for the per-step "
    "list and the official download references."
)


def _privilege_row() -> dict:
    """The single ``host_privileges`` row read from the privilege probe."""
    priv = (host_privileges.probe() or {}).get("content") or {}
    determined = not priv.get("undetermined", True)
    elevatable = bool(priv.get("elevated") or priv.get("can_elevate"))
    summary = str(priv.get("summary") or "no verdict")
    note = "" if determined else " [not verified on this host]"
    fix = "" if elevatable else _PRIVILEGE_FIX
    return rows.record_row(
        "host_privileges",
        rows._Row((not determined) or elevatable, summary + note, fix),
    )


# 12. host privileges.
#
# ONE PRODUCER, ONE MEANING, EVERY CONSUMER: the same verdict the
# qa_host_check tool and the virtualization fix text render. ADVISORY by
# construction (see ADVISORY_CHECKS above), so `ok` cannot see it.
# `undetermined` is reported as ok=True with the disclosure in `detail`:
# a probe that could not read the machine is not a finding about the
# machine, and rendering it as a failure is how a tester who CAN elevate
# gets sent to IT.
def _step_host_privileges(checks: list[dict]) -> None:
    """12. host privileges."""
    try:
        checks.append(_privilege_row())
    except _STEP_ERRORS as exc:
        checks.append(_failed("host_privileges", exc))
