"""Moved from handlers (refactor P2): _common."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise
from handlers import _lane_present
from handlers._common_boot import (  # noqa: F401
    _PROCESS_START,
    BOOT_ENV_FINGERPRINT,
    BOOT_VERSION,
    _env_fingerprint,
    _env_semantic_fingerprint,
)
from handlers._common_choice import (  # noqa: F401
    CHOSEN,
    DECLINED,
    UNAVAILABLE,
    ChoiceResult,
    ChooseCb,
    ProgressCb,
    unanswered,
)
from handlers._common_gates import (  # noqa: F401
    mobile_capture,
    rag_enabled,
    test_cases_only,
)
from handlers._common_misc import (  # noqa: F401
    _ACTOR,
    INSTALL_ROOT,
    _is_url,
    capture_error,
    emit,
)
from tools.audit_log import AuditEvent
from tools.audit_port import emit as record_event
from tools.device_manager import list_devices

# Pinned to the pre-split name: log filters, caplog and handlers key on it.
logger = logging.getLogger("tools.mcp_handlers")

# Hard cap on elicitation rounds per tool call so a wizard can never loop forever.
_MAX_ELICIT_ROUNDS = 5


async def audit(
    event_type: str, entity_id: str | None = None, detail: dict | None = None
) -> None:
    """Record one MCP audit event, swallowing any failure (never-raise)."""
    with never_raise(logger, "mcp audit record_event failed", exc_info=True):
        await record_event(
            AuditEvent(
                event_type, actor=_ACTOR, entity_id=entity_id, detail=detail or {}
            )
        )


async def resolve_device(device_id: str) -> dict | None:
    """Look up a full device dict (platform/kind) by id from live discovery.

    THE SEAM. Six call sites use this and ten tests patch it by name, so it
    stays the one lookup: a sibling that duplicated the search would take the
    call away from the patch, the tests would exercise real discovery without
    saying so, and that is the defect this branch already fixed once in
    `device_manager` (a patched `_list_android` that stopped being called).
    """
    if not device_id:
        return None
    result = await list_devices()
    for dev in result.get("content") or []:
        if dev.get("id") == device_id:
            return dev
    return None


async def _resolve_device(device_id: str) -> dict | None:
    """Call-time hop to ``resolve_device`` (the private name stays patchable)."""
    return await resolve_device(device_id)


# ``why_not`` is "" unless the device list was TRUNCATED, in which case a miss
# is not evidence the device is absent: `adb devices` output past the cap is
# never parsed, so an attached device can be missing from ``content``, and a
# bare "not found" tells the tester their id is wrong while pointing them at a
# list that cannot contain it either.
#
# It delegates rather than searching again, so the lookup keeps ONE
# implementation and one seam. The count comes from a second call, and that is
# deliberate: only the miss path pays for it, and the count is advisory text,
# not the answer. If discovery changes between the two calls the worst case is
# a caveat that no longer applies -- never a wrong device.
async def _resolve_device_with_reason(device_id: str) -> tuple[dict | None, str]:
    """``(device, why_not)`` -- :func:`_resolve_device` plus WHY a miss happened."""
    device = await _resolve_device(device_id)
    if device is not None or not device_id:
        return device, ""
    result = await list_devices()
    try:
        dropped = max(0, int(result.get("adb_dropped_lines") or 0))
    except (TypeError, ValueError, OverflowError):
        dropped = 0
    if dropped:
        return None, (
            " adb listed more device lines than this server reads, so "
            + str(dropped)
            + " were not parsed -- yours may be among them, and the list will"
            + " not show it either. Unplug what you are not testing and retry."
        )
    return None, ""


def mobile_modules_present() -> bool:
    """True when the mobile lane's packages shipped (``_lane_present`` probes it).

    ``scripts/build_dist.py`` excludes ``tools/mobile`` and ``handlers/mobile``
    together from a dist build whose checkout has no
    ``tools/mobile/ime_manifest.py`` (the qa-ime release is not cut yet, so
    the lane could never complete a preflight anyway). This is the
    registration-time half of that refusal: without it the tools would be
    registered and the first call would fail on an import instead of the tool
    simply not existing. ``handlers._lane_present`` is a leaf probe (no cycle with the lane).
    """

    return _lane_present.mobile_present()


# It is ``mobile_modules_present()`` ALONE, and it may not be re-expressed
# anywhere else -- ``mcp_server`` calls THIS, and a test asserts the
# registration site holds nothing but a call to it. A build made without
# the pinned IME ships no ``tools/mobile`` (``scripts/build_dist.py``), so
# module absence IS the edition gate. ``qa_mobile_https_capture_enabled`` is not a term: it gates only
# the capture download and the capture-certificate install, at those effects.
#
# * ``mobile_modules_present()`` is the CORRECTNESS term: a build whose
#   checkout had no ``tools/mobile/ime_manifest.py`` ships no ``tools/mobile``
#   at all (``scripts/build_dist.py`` excludes it), and a registered tool
#   whose first line cannot import is worse than an absent tool. It checks
#   the modules are really on disk rather than inferring it from anything.
#
# **2026-09-04 -- ``not test_cases_only()`` was a THIRD conjunct here, and it
# made the lane unreachable on every distribution build.** It was documented
# as a second statement of the correctness term above, but it does not state
# that: ``test_cases_only()`` is ``qa_dist_mode or not FULL_EDITION``, and
# ``FULL_EDITION`` is False on a dist because the bug-report and
# exploratory-coach agents are not shipped -- a fact about two unrelated
# agents. Measured on the published v1.77.0 install with
# the lane's then-flag switched on: 12 tools registered, none of them mobile,
# while the README promised three. Neutralising this one term in that same
# install produced all three. The whole point of shipping the lane and
# pinning the IME was to reach a tester, so the term is gone and
# ``mobile_modules_present()`` carries the argument alone -- which is what
# the mobile programme plan specified in the first place.
#
# Read fresh on every call, never cached: a test may patch the module probe,
# and a cached answer would make the absent path unobservable.
def mobile_lane_enabled() -> bool:
    """The ONE condition that decides whether the mobile lane exists."""
    return mobile_modules_present()
