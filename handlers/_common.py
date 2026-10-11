"""Moved from handlers (refactor P2): _common."""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Optional

from config.settings import settings
from handlers import _agents
from tools import telemetry
from tools.audit_log import record_event
from tools.device_manager import list_devices

# Pinned to the pre-split name: log filters, caplog and handlers key on it.
logger = logging.getLogger("tools.mcp_handlers")


def _boot_version() -> str:
    """The installed version as it was at IMPORT time. Compared against the
    on-disk VERSION later to notice that another process updated this install
    underneath a running server. Never raises."""
    try:
        from tools.updater import _INSTALL_DIR, _local_version

        return _local_version(Path(_INSTALL_DIR)) or ""
    except Exception:
        return ""


_BOOT_VERSION = _boot_version()


# Wall-clock at import: anything on disk newer than this was written AFTER the
# running server read its configuration.
_PROCESS_START = time.time()


def _env_semantic_lines(text: str) -> list[str]:
    """The SETTING lines of a .env: blank lines, full-line comments and
    surrounding whitespace removed."""
    out: list[str] = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


def _env_semantic_fingerprint(text: str) -> str:
    """sha256 over a .env's semantic lines. Never raises ("" on failure)."""
    try:
        digest = hashlib.sha256()
        for line in _env_semantic_lines(text):
            digest.update(line.encode("utf-8"))
            digest.update(b"|")
        return digest.hexdigest()
    except Exception:
        logger.debug("could not fingerprint .env content", exc_info=True)
        return ""


def _env_fingerprint() -> str:
    """Fingerprint of the install's .env ("" when absent or unreadable)."""
    try:
        from tools.updater import _INSTALL_DIR

        env_path = _INSTALL_DIR / ".env"
        if not env_path.is_file():
            return ""
        with env_path.open("r", encoding="utf-8", errors="replace") as handle:
            # Capped: a .env is a few KiB, and this runs on a tool call.
            raw = handle.read(1024 * 1024)
        return _env_semantic_fingerprint(raw)
    except Exception:
        logger.debug("could not read .env for the reload check", exc_info=True)
        return ""


# The .env this process actually booted with. Compared against, never re-read.
# NOTE: _PROCESS_START above is no longer read by THIS function -- do not delete
# it. tests/test_setup_check_env_reload.py and tests/test_setup_check_reload_marker.py
# still build their fixtures around it.
_BOOT_ENV_FINGERPRINT = _env_fingerprint()


def _test_cases_only() -> bool:
    """True when only the test-case tools should be exposed: the distribution
    build (optional modules absent) or QA_DIST_MODE=true."""
    return settings.qa_dist_mode or not _agents._FULL_EDITION


ProgressCb = Optional[Callable[[str], Awaitable[None]]]


# --- Guided-wizard choice callback (MCP elicitation bridge) ----------------- #
# mcp_server.py adapts FastMCP's ctx.elicit into this async callback, mirroring
# ProgressCb. It returns a ChoiceResult so these handlers stay importable and
# testable WITHOUT fastmcp and can react to accept / decline / (elicitation-)
# unavailable uniformly. A None callback (flag off / no client) is UNAVAILABLE.
CHOSEN = "chosen"
DECLINED = "declined"
UNAVAILABLE = "unavailable"


@dataclass
class ChoiceResult:
    """Outcome of one elicitation round. ``status`` is CHOSEN/DECLINED/UNAVAILABLE;
    ``value`` carries the selected option string when status is CHOSEN.

    2026-08-10 (K1): the two no-answer causes are recorded as FIELDS rather than as
    new ``status`` values, because ~20 call sites branch on
    ``status == UNAVAILABLE`` / ``== DECLINED`` and a new enum member would have
    changed all of them silently. Both default False, so every existing comparison
    is unaffected.

    ``timed_out``      -- the tester WAS asked and no answer arrived in time.
    ``budget_skipped`` -- the tester was NEVER asked: this call had already spent
                          its per-call elicitation budget on earlier dialogs.

    Keeping them apart matters because the messages differ: telling a tester "no
    answer arrived within 55s" when no dialog was ever shown is exactly the class of
    untrue tester-facing text this batch exists to remove."""

    status: str
    value: str | None = None
    timed_out: bool = False
    budget_skipped: bool = False


def _unanswered(result: "ChoiceResult") -> bool:
    """True when no answer came back for a reason the CALLER must handle.

    One predicate on purpose: the two flags always travel together at a decision
    point and always diverge at a wording point, and pairing them by hand at each
    site is how the pair drifts. Sites that must word the cause read the flags
    directly; sites that only need "did we get an answer" read this."""
    return bool(result.timed_out or result.budget_skipped)


ChooseCb = Optional[Callable[[str, list], Awaitable["ChoiceResult"]]]

# Hard cap on elicitation rounds per tool call so a wizard can never loop forever.
_MAX_ELICIT_ROUNDS = 5

# Actor recorded on every MCP audit event. The MCP transport carries no
# logged-in tester identity, so all its events share this actor.
_ACTOR = "mcp"

# Install root (…/qa-agents), used to anchor a RELATIVE qa_export_dir so the
# exported deliverable lands in the same folder whichever MCP client launched
# the server. See _resolved_export_dir.
_INSTALL_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #


async def _emit(progress: ProgressCb, message: str) -> None:
    """Best-effort progress ping — a failing/absent callback never breaks a tool."""
    if progress is None:
        return
    try:
        await progress(message)
    except Exception:
        logger.debug("mcp progress callback failed for %r", message, exc_info=True)


async def _audit(
    event_type: str, entity_id: str | None = None, detail: dict | None = None
) -> None:
    """Record one MCP audit event, swallowing any failure (never-raise)."""
    try:
        await record_event(
            event_type, actor=_ACTOR, entity_id=entity_id, detail=detail or {}
        )
    except Exception:
        logger.debug("mcp audit record_event failed", exc_info=True)


def _is_url(text: str) -> bool:
    return text.lower().startswith(("http://", "https://"))


def _capture_error(exc: BaseException, tool: str) -> None:
    """Best-effort dist error capture for a handler failure. Never raises; inert
    in the private checkout / with telemetry off (no dist PostHog key)."""
    try:
        telemetry.capture_error_dist(exc, tool=tool, origin="mcp_handler")
    except Exception:
        logger.debug("mcp telemetry capture_error failed", exc_info=True)


async def _resolve_device(device_id: str) -> dict | None:
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


async def _resolve_device_with_reason(device_id: str) -> tuple[dict | None, str]:
    """``(device, why_not)`` -- :func:`_resolve_device` plus WHY a miss happened.

    ``why_not`` is "" unless the device list was TRUNCATED, in which case a miss
    is not evidence the device is absent: `adb devices` output past the cap is
    never parsed, so an attached device can be missing from ``content``, and a
    bare "not found" tells the tester their id is wrong while pointing them at a
    list that cannot contain it either.

    It delegates rather than searching again, so the lookup keeps ONE
    implementation and one seam. The count comes from a second call, and that is
    deliberate: only the miss path pays for it, and the count is advisory text,
    not the answer. If discovery changes between the two calls the worst case is
    a caveat that no longer applies -- never a wrong device.
    """
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


def _elicit_enabled() -> bool:
    """Always True -- MCP elicitation dialogs are ON, unconditionally.

    QA_MCP_ELICIT_ENABLED was DELETED on 2026-08-13 (flag-surface reduction,
    batch 7 (needs-config)) and hardcoded to the value the DISTRIBUTION already
    shipped. A named seam, mirroring ``mcp_server._elicit_enabled``, so the
    markdown-menu fallback below every dialog stays executable by its tests.
    NOT settings-derived. A client that cannot show dialogs is UNAFFECTED: its
    ``ctx.elicit`` raises, the callback reports UNAVAILABLE, and the caller
    renders the menu exactly as it did with the flag off.
    """
    return True


def _mobile_capture() -> bool:
    """Always True -- device screen capture is ON, unconditionally.

    QA_MOBILE_CAPTURE was DELETED on 2026-08-13 and hardcoded to the `true` the
    dist .env.example already shipped. A named seam so the capture-disabled
    branches stay executable by their tests (they are unreachable in
    production), and so a test suite never shells out to `adb` by default.
    NOT settings-derived.
    """
    return True


def _mobile_modules_present() -> bool:
    """True when ``tools/mobile`` actually exists on disk (2026-09-03, D5).

    ``scripts/build_dist.py`` now excludes ``tools/mobile/*`` from a dist build
    whose checkout has no ``tools/mobile/ime_manifest.py`` (the qa-ime release
    is not cut yet, so the lane could never complete a preflight anyway). This
    is the registration-time half of that same refusal: without it, an
    operator who switched the lane on for such a build would still
    see the three tools registered, and the first call would fail on the
    function-local ``tools.mobile`` import inside ``handle_mobile_test`` -- an
    ugly generic error instead of the tool simply not existing. Uses
    ``find_spec`` rather than importing the package, so this stays cheap and
    never triggers the heavier imports ``tools/mobile/session_core.py`` pulls in.
    """
    import importlib.util

    try:
        return importlib.util.find_spec("tools.mobile.session_core") is not None
    except (ImportError, ValueError):
        return False


def _mobile_lane_enabled() -> bool:
    """The ONE condition that decides whether the mobile lane exists.

    It is ``_mobile_modules_present()`` ALONE, and it may not be re-expressed
    anywhere else -- ``mcp_server`` calls THIS, and a test asserts the
    registration site holds nothing but a call to it. A build made without
    the pinned IME ships no ``tools/mobile`` (``scripts/build_dist.py``), so
    module absence IS the edition gate. ``qa_mobile_https_capture_enabled`` is not a term: it gates only
    the capture download and the capture-certificate install, at those effects.

    * ``_mobile_modules_present()`` is the CORRECTNESS term: a build whose
      checkout had no ``tools/mobile/ime_manifest.py`` ships no ``tools/mobile``
      at all (``scripts/build_dist.py`` excludes it), and a registered tool
      whose first line cannot import is worse than an absent tool. It checks
      the modules are really on disk rather than inferring it from anything.

    **2026-09-04 -- ``not _test_cases_only()`` was a THIRD conjunct here, and it
    made the lane unreachable on every distribution build.** It was documented
    as a second statement of the correctness term above, but it does not state
    that: ``_test_cases_only()`` is ``qa_dist_mode or not _FULL_EDITION``, and
    ``_FULL_EDITION`` is False on a dist because the bug-report and
    exploratory-coach agents are not shipped -- a fact about two unrelated
    agents. Measured on the published v1.77.0 install with
    the lane's then-flag switched on: 12 tools registered, none of them mobile,
    while the README promised three. Neutralising this one term in that same
    install produced all three. The whole point of shipping the lane and
    pinning the IME was to reach a tester, so the term is gone and
    ``_mobile_modules_present()`` carries the argument alone -- which is what
    ``.claude/plans/mobile-programme.md`` specified in the first place.

    Read fresh on every call, never cached: a test may patch the module probe,
    and a cached answer would make the absent path unobservable.
    """
    return _mobile_modules_present()


def _rag_enabled() -> bool:
    """Always True -- RAG corpus grounding is ON, unconditionally.

    QA_RAG_ENABLED was DELETED on 2026-08-13 and hardcoded to the `true` the
    dist .env.example already shipped. An EMPTY corpus was always a silent
    no-op, which is what made the per-install switch redundant. NOT
    settings-derived.
    """
    return True


def _feature_analysis_enabled() -> bool:
    """Always True -- Feature Analysis is ON, unconditionally.

    QA_FEATURE_ANALYSIS_ENABLED was DELETED on 2026-08-14 (flag-surface
    reduction, batch 8c) and hardcoded ON. A named seam, mirroring
    ``mcp_server._feature_analysis_enabled``. NOT settings-derived.

    Every caller still checks ``_test_cases_only()`` FIRST: the edition gate
    outranks this, so the public distribution is unaffected.
    """
    return True
